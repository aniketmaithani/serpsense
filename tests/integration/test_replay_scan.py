"""A whole scan in replay mode on real Postgres, with no SerpApi or Anthropic key: the recorded
answers are collected, parsed, labelled as recorded, scored, and nothing is billed."""

import json
import uuid
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text

from serpsense.adapters.cache.null_cache import NullResponseCache
from serpsense.adapters.db.llm_ledger import SqlLlmLedger
from serpsense.adapters.db.llm_profiles import SqlLlmProfiles
from serpsense.adapters.db.search_ledger import SqlSearchLedger
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.adapters.llm.replay import ReplayLlm
from serpsense.adapters.replay.recording import (
    RecordedAnswer,
    RecordedLabel,
    RecordedScan,
    Recording,
    text_id,
)
from serpsense.adapters.serp.collectors import COLLECTORS
from serpsense.adapters.serp.parsers import parse_news
from serpsense.adapters.serp.replay import ReplaySearchProvider
from serpsense.domain.enums import ScanStatus, SerpEngine, Topic
from serpsense.domain.llm_capabilities import LlmPreset
from serpsense.domain.settings.search import SearchSettings
from serpsense.ports.clock import Clock
from serpsense.ports.collector import App, Subject, Target
from serpsense.ports.search_provider import SearchRequest
from serpsense.services.collection import CollectorRunner
from serpsense.services.demo import seed_demo
from serpsense.services.dispatch import Dispatcher
from serpsense.services.grouping import Grouper
from serpsense.services.labelling import Labeller
from serpsense.services.llm_gateway import LlmGateway
from serpsense.services.scans import ScanLimits, ScanPorts, ScanService
from serpsense.services.search import SearchLimits, SearchPorts, SearchService
from tests.fakes import FixedClock, RecordingJobs
from tests.integration.db_helpers import NOW, table

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures" / "serpapi" / "ola"
HALF_DAY = timedelta(hours=12)
NEWS = SerpEngine.GOOGLE_NEWS
SCORED = text("SELECT scan_id FROM v_scan_scores WHERE scan_id = ANY(:scans)")


def recorded(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return payload


def fixture_for(request: SearchRequest) -> str:
    """Which recorded Ola response answers a request the collectors make."""
    params = request.params
    match request.engine:
        case SerpEngine.GOOGLE_TRENDS:
            return "trends_timeseries" if params["data_type"] == "TIMESERIES" else "trends_related"
        case SerpEngine.GOOGLE_PLAY_PRODUCT:
            return "play_reviews" if "all_reviews" in params else "play_product"
        case SerpEngine.GOOGLE_NEWS:
            return "news"
        case SerpEngine.GOOGLE_AUTOCOMPLETE:
            return "autocomplete"
    return "google"


def requests_of(uow: SqlUnitOfWork, scan_id: uuid.UUID) -> list[SearchRequest]:
    """The requests the scan will make, as the scan service aims its collectors."""
    target = uow.targets.for_scan(scan_id)
    assert target is not None
    aim = Target(
        Subject(target.brand.brand_id, target.brand.name),
        SearchSettings.model_validate(dict(target.settings_snapshot)),
        competitors=tuple(Subject(c.brand_id, c.name) for c in target.competitors),
        apps=tuple(App(a.brand_app_id, a.package) for a in target.apps),
    )
    return [lead.request for collector in COLLECTORS for lead in collector.leads(aim)]


def recording_for(
    requests: list[SearchRequest], settings: dict[str, Any] | None = None
) -> Recording:
    """Two recorded scans, half a day apart: the second found only the first news story."""
    names = sorted({fixture_for(r) for r in requests})
    news = recorded("news")
    payloads = [recorded(name) for name in names]
    payloads.append({**news, "news_results": news["news_results"][:1]})
    scans = [
        RecordedScan(
            recorded_at=NOW + n * HALF_DAY,
            settings=settings or {},
            answers=tuple(
                RecordedAnswer(
                    engine=r.engine,
                    params={key: str(value) for key, value in r.params.items()},
                    payload=len(names) if n and r.engine is NEWS else names.index(fixture_for(r)),
                )
                for r in requests
            ),
        )
        for n in (0, 1)
    ]
    first = parse_news(news)[0]
    label = RecordedLabel(
        text_id=text_id(first.source, first.text),
        prompt_version="label_mentions/v1",
        is_about_brand=True,
        sentiment=-1,
        topic=Topic.RELIABILITY,
        is_complaint=True,
        severity=60,
        reason="Recorded: riders complain.",
    )
    return Recording(
        format=1,
        brand="ola",
        name="Ola",
        payloads=tuple(payloads),
        scans=tuple(scans),
        labels=(label,),
    )


def scanner(engine: Engine, recordings: Sequence[Recording], clock: Clock) -> ScanService:
    """A scan service on replay sources, as the replay worker wires it."""

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine, RecordingJobs())

    ledger = SqlSearchLedger(engine.begin)
    provider = ReplaySearchProvider(recordings, clock)
    limits = SearchLimits(monthly_default=1500, global_daily=500, per_scan=40)
    search = SearchService(SearchPorts(provider, ledger, NullResponseCache(), clock), limits)
    # A budget of one micro: replayed calls cost nothing, so they never spend it.
    gateway = LlmGateway(
        ReplayLlm(recordings), SqlLlmLedger(engine.begin), clock, monthly_budget_micros=lambda _: 1
    )
    ports = ScanPorts(
        unit_of_work=unit_of_work,
        collector=CollectorRunner(COLLECTORS, search, clock),
        labeller=Labeller(unit_of_work, gateway, clock),
        grouper=Grouper(unit_of_work, gateway, clock),
        usage=ledger,
        profiles=SqlLlmProfiles(engine, LlmPreset.BALANCED),
        clock=clock,
    )
    limits = ScanLimits(
        time_limit=timedelta(minutes=10), enrich_time=timedelta(minutes=14), monthly_searches=1500
    )
    return ScanService(ports, limits)


def test_replayed_scans_run_the_whole_pipeline_and_bill_nothing(committing_engine: Engine) -> None:
    jobs, clock = RecordingJobs(), FixedClock(NOW)

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(committing_engine, jobs)

    email = f"replay-{uuid.uuid4().hex[:8]}@example.com"
    seeded = seed_demo(unit_of_work, clock, owner_email=email)
    Dispatcher(unit_of_work, clock, max_searches_per_scan=40).dispatch()
    scans = table("scans")
    ola = select(scans.c.id).where(scans.c.brand_id == seeded.brand_id)
    with committing_engine.connect() as conn:
        first = conn.execute(ola).scalar_one()
    with unit_of_work() as uow:
        recording = recording_for(requests_of(uow, first))

    service = scanner(committing_engine, [recording], clock)
    assert service.run(first) is ScanStatus.SUCCEEDED  # at the first scan's recorded time
    clock.at = NOW + HALF_DAY  # the next slot of Ola's 12-hour schedule, and the second scan
    Dispatcher(unit_of_work, clock, max_searches_per_scan=40).dispatch()
    with committing_engine.connect() as conn:
        second = conn.execute(ola.where(scans.c.id != first)).scalar_one()
    assert service.run(second) is ScanStatus.SUCCEEDED

    calls, sightings = table("serp_calls"), table("mention_observations")
    mentions, labels = table("mentions"), table("enrichments")
    both = [first, second]
    with committing_engine.connect() as conn:
        served = conn.execute(select(calls.c.served_from).where(calls.c.scan_id.in_(both)))
        assert set(served.scalars()) == {"serpapi_cache"}  # never billed
        news_seen = (
            select(sightings.c.scan_id, func.count())
            .join(mentions, mentions.c.id == sightings.c.mention_id)
            .where(sightings.c.scan_id.in_(both), mentions.c.source == "news")
            .group_by(sightings.c.scan_id)
        )
        seen: dict[uuid.UUID, int] = {scan: count for scan, count in conn.execute(news_seen)}
        assert seen[second] == 1 < seen[first]  # the second scan replayed the second recording
        brand_labels = (
            select(labels.c.sentiment, labels.c.reason, labels.c.is_about_brand)
            .join(mentions, mentions.c.id == labels.c.mention_id)
            .where(mentions.c.brand_id == seeded.brand_id)
        )
        got = conn.execute(brand_labels).all()
        assert got == [(-1, "Recorded: riders complain.", True)]  # nothing else is made up
        replayed = select(table("llm_calls").c.served_model, table("llm_calls").c.cost_micros)
        assert set(conn.execute(replayed.where(table("llm_calls").c.scan_id.in_(both)))) == {
            ("replay", 0)
        }
        assert set(conn.execute(SCORED, {"scans": both}).scalars()) == set(both)
