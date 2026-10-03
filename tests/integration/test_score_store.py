"""The score store on real Postgres: what scoring reads from a brand's scans, and the scores,
written once."""

import hashlib
import uuid
from collections import Counter
from datetime import timedelta
from types import MappingProxyType
from typing import Any

import pytest
from sqlalchemy import Connection, func, insert, select, text

from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.adapters.db.score_store import SqlScoreStore
from serpsense.domain.enums import CrisisComponent, LlmTask, MentionSource, Surface, Topic
from serpsense.domain.scoring.scan import Observed, ScanScores
from serpsense.ports.enrichment_store import MentionLabel
from serpsense.ports.score_store import ScoreStore
from tests.integration.db_helpers import (
    NOW,
    add_app,
    add_brand,
    add_llm_call,
    add_mention,
    add_owned_brand,
    add_scan,
    add_user,
    table,
)

pytestmark = pytest.mark.integration

QUIET = MappingProxyType({c: 0 for c in CrisisComponent})
PROMPT = "label_mentions/v1"
PROMPTS = {
    LlmTask.LABEL_MENTIONS: PROMPT,
    LlmTask.CLASSIFY_AUTOCOMPLETE: "classify_autocomplete/v1",
}
HOUR = timedelta(hours=1)
VIEW = text("SELECT health, crisis, crisis_level FROM v_scan_scores WHERE scan_id = :id")


@pytest.fixture
def store(conn: Connection) -> ScoreStore:
    return SqlScoreStore(conn)


def count(conn: Connection, name: str) -> int:
    return conn.execute(select(func.count()).select_from(table(name))).scalar_one()


def test_scores_are_recorded_once_and_the_view_reads_them(
    conn: Connection, store: ScoreStore
) -> None:
    scan_id = add_scan(conn, add_owned_brand(conn), status="running")
    spike = QUIET | {CrisisComponent.PRESS: 80}
    scores = ScanScores(MappingProxyType({Surface.NEWS: 50, Surface.PLAY: 70}), spike)

    assert store.record(scan_id, scores, version="s1", at=NOW) is True
    again = ScanScores(MappingProxyType({Surface.NEWS: 0}), QUIET)
    later = NOW + HOUR
    assert store.record(scan_id, again, version="s1", at=later) is False  # the first stay
    assert tuple(conn.execute(VIEW, {"id": scan_id}).one()) == (
        60,
        8,
        None,
    )  # equal weights; warming up
    assert (count(conn, "surface_scores"), count(conn, "crisis_components")) == (2, 5)


def test_a_scan_that_showed_nothing_still_records_its_components(
    conn: Connection, store: ScoreStore
) -> None:
    scan_id = add_scan(conn, add_owned_brand(conn), status="running")
    assert store.record(scan_id, ScanScores(MappingProxyType({}), QUIET), version="s1", at=NOW)
    assert tuple(conn.execute(VIEW, {"id": scan_id}).one()) == (None, 0, None)
    assert (count(conn, "surface_scores"), count(conn, "crisis_components")) == (0, 5)


class Brand:
    """One brand's scans, mentions and labels, built row by row."""

    def __init__(self, conn: Connection, slug: str = "voltbox") -> None:
        self.conn = conn
        self.owner = add_user(conn, f"{slug}@example.com")
        self.id = add_brand(conn, self.owner, slug=slug)
        self.call = add_llm_call(conn, self.owner)

    def scan(self, hours: int, *collected: Surface, scored: bool = False) -> uuid.UUID:
        at = NOW + hours * HOUR
        status = "running" if hours == 0 else "succeeded"
        scan_id = add_scan(self.conn, self.id, status=status, scheduled_for=at, created_at=at)
        results = [{"scan_id": scan_id, "surface": s, "outcome": "succeeded"} for s in collected]
        if results:
            self.conn.execute(insert(table("scan_surface_results")), results)
        if scored:
            SqlScoreStore(self.conn).record(scan_id, ScanScores({}, QUIET), version="s1", at=at)
        return scan_id

    def mention(self, name: str, source: MentionSource, **values: Any) -> uuid.UUID:
        key = hashlib.sha256(name.encode()).hexdigest()
        url = None if source in {MentionSource.PLAY_REVIEW, MentionSource.AUTOCOMPLETE} else name
        values = {"url": url, "outlet": None, "brand_app_id": None, "text": name, **values}
        return add_mention(self.conn, self.id, source=source, identity_key=key, **values)

    def seen(self, mention_id: uuid.UUID, *scans: uuid.UUID, position: int | None = None) -> None:
        for scan_id in scans:
            row = {"mention_id": mention_id, "scan_id": scan_id, "position": position}
            self.conn.execute(insert(table("mention_observations")).values(**row))

    def label(self, mention_id: uuid.UUID, sentiment: int, **values: Any) -> None:
        fields: dict[str, Any] = {"revision": 1, "severity": 20, "is_about_brand": True} | values
        prompt, at = fields.pop("prompt", PROMPT), fields.pop("at", NOW)
        label = MentionLabel(
            mention_id, sentiment=sentiment, topic=Topic.PRICING, is_complaint=sentiment < 0,
            reason="Fares.", **fields,
        )  # fmt: skip
        call = (
            self.call
            if prompt == PROMPT
            else add_llm_call(
                self.conn, self.owner, task=prompt.split("/")[0], prompt_version=prompt
            )
        )
        store = SqlEnrichmentStore(self.conn)
        store.record([label], prompt_version=prompt, llm_call_id=call, at=at)


def history(conn: Connection) -> uuid.UUID:
    """A brand's scans around the one being scored (returned), and what each saw."""
    brand = Brand(conn)
    first = brand.scan(-24, Surface.SEARCH_PAGE, Surface.NEWS, scored=True)
    unscored = brand.scan(-18, Surface.AUTOCOMPLETE)  # collected, but never scored
    second = brand.scan(-12, Surface.NEWS, Surface.PLAY, scored=True)
    now = brand.scan(0, Surface.NEWS, Surface.YOUTUBE)  # its own surfaces aren't "before"
    brand.scan(12, Surface.MAPS, scored=True)  # a later scan is neither earlier nor collected

    old = brand.mention("https://n.in/old", MentionSource.NEWS)
    brand.seen(old, first, second, now)
    brand.label(old, -1)
    rising = brand.mention("https://n.in/rising", MentionSource.NEWS)
    brand.seen(rising, second, now)
    brand.label(rising, -1)
    calm = brand.mention("https://n.in/calm", MentionSource.NEWS)
    brand.seen(calm, second)  # neutral: not one of the scan's negatives
    brand.label(calm, 0)
    between = brand.mention("https://n.in/between", MentionSource.NEWS)
    brand.seen(between, unscored, now)  # first seen in a scan that wasn't scored
    brand.label(between, -1)
    fresh = brand.mention("https://n.in/fresh", MentionSource.NEWS, published_at=NOW - HOUR)
    brand.seen(fresh, now, position=2)
    brand.label(fresh, -1, severity=60)

    app = add_app(conn, brand.id)
    play = {"brand_app_id": app}
    edited = brand.mention("gp:1", MentionSource.PLAY_REVIEW, published_at=NOW - 2 * HOUR, **play)
    brand.seen(edited, second, now)
    brand.label(edited, -1)  # the first text was a complaint ...
    revised = {"mention_id": edited, "revision": 2, "text": "Fixed", "scan_id": now}
    conn.execute(insert(table("mention_revisions")).values(created_at=NOW, **revised))
    brand.label(edited, 1, revision=2)  # ... the edit isn't
    latest = brand.mention("gp:2", MentionSource.PLAY_REVIEW, published_at=NOW - HOUR, **play)
    brand.seen(latest, now)
    brand.label(latest, -1)
    rating = {"brand_app_id": app, "scan_id": now, "rating_hundredths": 420, "review_count": 9}
    conn.execute(insert(table("app_rating_observations")).values(**rating))

    greeting = brand.mention("https://n.in/hello", MentionSource.SERP_RESULT)
    brand.seen(greeting, now, position=1)
    brand.label(greeting, 1, is_about_brand=False)  # "ola" the greeting: left out
    unlabelled = brand.mention("ola cancel fee", MentionSource.AUTOCOMPLETE)
    brand.seen(unlabelled, now, position=1)
    suggestion = brand.mention("ola fare hike", MentionSource.AUTOCOMPLETE)
    brand.seen(suggestion, now, position=3)
    brand.label(suggestion, -1, prompt="classify_autocomplete/v1")  # its own task's prompt
    rival = Brand(conn, "uber")  # another brand's scored scan, and a surface only it collected
    rival.seen(
        rival.mention("https://n.in/uber", MentionSource.NEWS),
        rival.scan(-6, Surface.TRENDS, scored=True),
    )
    return now


def test_inputs_are_the_labelled_sightings_and_the_surfaces_collected_before(
    conn: Connection, store: ScoreStore
) -> None:
    inputs = store.inputs(history(conn), prompts=PROMPTS)

    news = MentionSource.NEWS
    assert inputs.at == NOW
    assert Counter(inputs.observed) == {
        Observed(news, -1, 60, 2, NOW - HOUR, new=True): 1,
        Observed(MentionSource.AUTOCOMPLETE, -1, 20, 3, NOW, new=True): 1,
        Observed(news, -1, 20, None, NOW): 3,  # seen first in earlier scans, scored or not
        Observed(MentionSource.PLAY_REVIEW, 1, 20, None, NOW - 2 * HOUR): 1,  # edited since
        Observed(MentionSource.PLAY_REVIEW, -1, 20, None, NOW - HOUR, new=True): 1,
    }
    assert inputs.observed[0] == Observed(news, -1, 60, 2, NOW - HOUR, new=True)  # ranked first
    assert inputs.ratings == (420,)
    assert inputs.newest_reviews == (-1, 1)  # newest first, by the edited text's label
    collected = {Surface.SEARCH_PAGE, Surface.NEWS, Surface.AUTOCOMPLETE}  # unscored ones too
    assert inputs.collected_before == collected | {Surface.PLAY}


def test_a_label_is_from_the_mentions_own_task_the_active_prompts_first(
    conn: Connection, store: ScoreStore
) -> None:
    brand = Brand(conn)
    now = brand.scan(0, Surface.NEWS)
    v1, v2 = "label_mentions/v1", "label_mentions/v2"
    relabelled = brand.mention("https://n.in/a", MentionSource.NEWS)
    brand.seen(relabelled, now, position=1)
    brand.label(relabelled, -1, prompt=v2)  # the active prompt's label wins ...
    brand.label(relabelled, 1, prompt=v1, at=NOW + HOUR)  # ... over a newer one of another
    waiting = brand.mention("https://n.in/b", MentionSource.NEWS)
    brand.seen(waiting, now, position=2)
    brand.label(waiting, 0, prompt=v1)  # not labelled by the active prompt yet: this one stands
    misfiled = brand.mention("ola fares", MentionSource.AUTOCOMPLETE)
    brand.seen(misfiled, now, position=3)
    brand.label(misfiled, -1, prompt=v2)  # another task's prompt: not a label of a suggestion

    inputs = store.inputs(now, prompts={LlmTask.LABEL_MENTIONS: v2})
    assert [(o.sentiment, o.position) for o in inputs.observed] == [(-1, 1), (0, 2)]
