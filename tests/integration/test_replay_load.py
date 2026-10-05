"""Playing recordings into a fresh install on real Postgres (BUILD_PLAN §23): seeded for replay,
every recorded scan played at its time, once, and Scan now repeating the last answers."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text

from serpsense.adapters.db.search_ledger import SqlSearchLedger
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.adapters.replay.clock import ReplayClock
from serpsense.adapters.replay.story.build import recordings as story_recordings
from serpsense.adapters.serp.collectors import COLLECTORS
from serpsense.composition import build_celery
from serpsense.composition_replay import build_replayer
from serpsense.domain.settings.search import SearchSettings, resolve
from serpsense.ports.collector import App, Subject, Target
from serpsense.ports.search_provider import SearchRequest
from serpsense.services.demo import OLA, Seeded, seed_demo
from serpsense.services.drafts import Outcome
from serpsense.services.replay import Played, RecordedScan, ReplayLoader
from serpsense.services.scan_now import Requested, ScanNow, ScanNowLimits
from tests.factories import make_settings
from tests.fakes import FixedClock, RecordingJobs
from tests.integration.db_helpers import NOW, table
from tests.integration.test_replay_scan import HALF_DAY, recording_for, scanner

pytestmark = pytest.mark.integration

RECORDED: dict[str, Any] = resolve(OLA.settings).model_dump(mode="json")  # a scan's snapshot
SCANS, BRANDS, APPS = table("scans"), table("brands"), table("brand_apps")
SCHEDULES, DOCUMENTS = table("brand_schedule_versions"), table("brand_search_settings_versions")
# What the demo story leaves on the main brand's pages, and on its competitor's.
STORY_SHOWS = text(
    """
SELECT
  (SELECT max(v.crisis_level) FROM v_scan_scores v WHERE v.brand_id = :brand) AS highest,
  EXISTS (SELECT 1 FROM v_scan_scores v WHERE v.brand_id = :brand AND v.crisis_level = 'medium')
    AS medium,
  ARRAY(SELECT DISTINCT a.rule::text FROM alerts a JOIN scans s ON s.id = a.scan_id
        WHERE s.brand_id = :brand ORDER BY 1) AS rules,
  ARRAY(SELECT n.label FROM narratives n WHERE n.brand_id = :brand ORDER BY 1) AS stories,
  (SELECT max(v.crisis) FROM v_scan_scores v JOIN brand_competitors c
     ON c.competitor_brand_id = v.brand_id WHERE c.brand_id = :brand) AS rival_crisis
"""
)
SCORED = text("SELECT count(*) FROM v_scan_scores WHERE scan_id = ANY(:scans)")


def ola_requests(engine: Engine, seeded: Seeded) -> list[SearchRequest]:
    """What Ola's scans ask, as the scan service aims its collectors (rivals by name)."""
    with engine.connect() as conn:
        rivals = conn.execute(
            select(BRANDS.c.id, BRANDS.c.name)
            .where(BRANDS.c.id.in_(seeded.competitor_ids))
            .order_by(BRANDS.c.name)
        ).all()
        app = conn.execute(select(APPS.c.id).where(APPS.c.brand_id == seeded.brand_id)).scalar_one()
    aim = Target(
        Subject(seeded.brand_id, "Ola"),
        SearchSettings.model_validate(RECORDED),
        competitors=tuple(Subject(brand, name) for brand, name in rivals),
        apps=(App(app, OLA.app),),
    )
    return [lead.request for collector in COLLECTORS for lead in collector.leads(aim)]


def test_a_fresh_install_gets_every_recorded_scan_once(committing_engine: Engine) -> None:
    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(committing_engine, RecordingJobs())

    email = f"replay-{uuid.uuid4().hex[:8]}@example.com"
    replay = {"ola": RECORDED}
    seeded = seed_demo(unit_of_work, FixedClock(NOW - HALF_DAY), owner_email=email, replay=replay)
    ours = (seeded.brand_id, *seeded.competitor_ids)
    with committing_engine.connect() as conn:
        intervals = select(SCHEDULES.c.interval_minutes).where(SCHEDULES.c.brand_id.in_(ours))
        assert set(conn.execute(intervals).scalars()) == {None}  # scanned on request only
        documents = select(DOCUMENTS.c.document).where(DOCUMENTS.c.brand_id == seeded.brand_id)
        assert conn.execute(documents).scalar_one() == RECORDED  # as the recording was made

    recording = recording_for(ola_requests(committing_engine, seeded), RECORDED)
    plan = [RecordedScan("ola", scan.recorded_at, scan.settings) for scan in recording.scans]
    clock = ReplayClock()
    loader = ReplayLoader(unit_of_work, scanner(committing_engine, [recording], clock), clock)
    assert loader.load(seeded.owner_id, {"ola": seeded.brand_id}, plan) == {Played.PLAYED: 2}
    assert loader.load(seeded.owner_id, {"ola": seeded.brand_id}, plan) == {Played.ALREADY: 2}

    replayed = select(SCANS.c.id, SCANS.c.created_at, SCANS.c.status, SCANS.c.trigger).where(
        SCANS.c.brand_id == seeded.brand_id
    )
    with committing_engine.connect() as conn:
        rows = conn.execute(replayed.order_by(SCANS.c.created_at)).all()
        assert [(r.created_at, r.status, r.trigger) for r in rows] == [
            (NOW, "succeeded", "replay"),
            (NOW + HALF_DAY, "succeeded", "replay"),
        ]
        assert conn.execute(SCORED, {"scans": [r.id for r in rows]}).scalar_one() == 2

    later = NOW + timedelta(days=3)  # Scan now, after the load: the last answers again
    searches = SqlSearchLedger(committing_engine.begin)
    scan_now = ScanNow(unit_of_work, searches, FixedClock(later), ScanNowLimits(40, 240))
    assert scan_now.request(seeded.owner_id, seeded.brand_id) is Requested.QUEUED
    with committing_engine.connect() as conn:
        manual = conn.execute(replayed.where(SCANS.c.trigger == "manual")).one()
    assert scanner(committing_engine, [recording], FixedClock(later)).run(manual.id) is not None
    sightings, mentions = table("mention_observations"), table("mentions")
    news = (
        select(func.count())
        .select_from(sightings)
        .join(mentions, mentions.c.id == sightings.c.mention_id)
        .where(sightings.c.scan_id == manual.id, mentions.c.source == "news")
    )
    with committing_engine.connect() as conn:
        assert conn.execute(news).scalar_one() == 1  # the second recorded scan's news
        status = select(SCANS.c.status).where(SCANS.c.id == manual.id)
        assert conn.execute(status).scalar_one() == "succeeded"


def test_the_replayer_seeds_the_demo_story_and_plays_it_through(
    committing_engine: Engine, redis_url: str
) -> None:
    url = committing_engine.url.render_as_string(hide_password=False)
    settings = make_settings(database_url=url, serpsense_mode="replay", redis_url=redis_url)
    replay = build_replayer(settings, build_celery(settings))  # alerts nudge the explainer
    email = f"replayer-{uuid.uuid4().hex[:8]}@example.com"
    replayed = replay(email)
    story = sum(len(r.scans) for r in story_recordings())
    assert replayed.played[Played.PLAYED] == story  # every scan of the story
    assert replayed.drafted is Outcome.DRAFTED  # a holding statement on the Drafts page
    again = replay(email)
    assert again.seeded == replayed.seeded and again.played[Played.PLAYED] == 0
    assert again.drafted is None  # the owner has a draft now, so no second one
    ours = (replayed.seeded.brand_id, *replayed.seeded.competitor_ids)
    with committing_engine.connect() as conn:
        intervals = select(SCHEDULES.c.interval_minutes).where(SCHEDULES.c.brand_id.in_(ours))
        assert set(conn.execute(intervals).scalars()) == {None}
        shown = conn.execute(STORY_SHOWS, {"brand": replayed.seeded.brand_id}).one()
    assert shown.highest == "high" and shown.medium  # the crisis builds, then peaks
    assert shown.rules == ["level_increase", "narrative_spread"]
    assert shown.stories == ["Battery swelling", "Slow refunds"]
    assert shown.rival_crisis == 0  # SoundNest is untouched: the trouble is VoltBox's own
