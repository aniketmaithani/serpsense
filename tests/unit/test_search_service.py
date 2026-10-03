"""The search service: cache, circuit breaker, budgets and retries, with one ledger row per
attempt or skipped call (ADR-0007, data-model §5)."""

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from serpsense.domain.enums import SerpCallOutcome, SerpEngine, SerpErrorCode, ServedFrom
from serpsense.domain.usage import day_start, month_start
from serpsense.ports.search_ledger import CallRecord
from serpsense.ports.search_provider import SearchFailed, SearchRequest, SearchResponse
from serpsense.services.search import (
    SearchLimits,
    SearchPorts,
    SearchService,
    SearchSkipped,
    SkipReason,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
USER, SCAN = uuid.uuid4(), uuid.uuid4()
NEWS = SearchRequest(engine=SerpEngine.GOOGLE_NEWS, params={"q": "Ola"})
LIMITS = SearchLimits(monthly_default=10, global_daily=100, per_scan=5)
PAYLOAD = {"news_results": [{"title": "Ola fares"}]}
LIVE = SearchResponse(payload=PAYLOAD, served_from=ServedFrom.LIVE, http_status=200, latency_ms=80)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeProvider:
    def __init__(self, *script: SearchResponse | SearchFailed) -> None:
        self.script, self.calls = list(script), 0

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, SearchFailed):
            raise step
        return step


class FakeLedger:
    def __init__(self, budget: int | None = None) -> None:
        self.calls: list[CallRecord] = []
        self.payloads: dict[uuid.UUID, Mapping[str, Any]] = {}
        self.ids: list[uuid.UUID] = []
        self.budget = budget

    def record_call(self, call: CallRecord) -> uuid.UUID:
        self.calls.append(call)
        self.ids.append(uuid.uuid4())
        return self.ids[-1]

    def store_payload(
        self, call_id: uuid.UUID, payload: Mapping[str, Any], *, at: datetime
    ) -> None:
        self.payloads[call_id] = payload

    def recent_attempts(
        self, engine: SerpEngine, *, since: datetime, limit: int
    ) -> Sequence[SerpErrorCode | None]:
        reached = [
            c
            for c in self.calls
            if c.request.engine is engine
            and c.created_at >= since
            and (
                c.outcome is SerpCallOutcome.FAILED
                or (
                    c.outcome is SerpCallOutcome.SUCCEEDED
                    and c.served_from is not ServedFrom.LOCAL_CACHE
                )
            )
        ]
        return [c.error_code for c in sorted(reached, key=lambda c: c.created_at, reverse=True)][
            :limit
        ]

    def live_calls(
        self, *, since: datetime, user_id: uuid.UUID | None = None, scan_id: uuid.UUID | None = None
    ) -> int:
        return sum(
            c.served_from is ServedFrom.LIVE
            and c.created_at >= since
            and user_id in (None, c.user_id)
            and scan_id in (None, c.scan_id)
            for c in self.calls
        )

    def monthly_budget(self, user_id: uuid.UUID, *, at: datetime) -> int | None:
        return self.budget


class FakeCache:
    def __init__(self, **entries: Mapping[str, Any]) -> None:
        self.entries: dict[str, Mapping[str, Any]] = dict(entries)
        self.ttls: dict[str, timedelta] = {}

    def get(self, key: str) -> Mapping[str, Any] | None:
        return self.entries.get(key)

    def set(self, key: str, payload: Mapping[str, Any], *, ttl: timedelta) -> None:
        self.entries[key], self.ttls[key] = payload, ttl


def failure(code: SerpErrorCode) -> SearchFailed:
    status = None if code in {SerpErrorCode.NETWORK, SerpErrorCode.TIMEOUT} else 503
    return SearchFailed(code, http_status=status, latency_ms=30_000)


def live_call(created_at: datetime, **overrides: Any) -> CallRecord:
    values: dict[str, Any] = {
        "user_id": USER,
        "scan_id": None,
        "request": NEWS,
        "outcome": SerpCallOutcome.SUCCEEDED,
        "served_from": ServedFrom.LIVE,
        "http_status": 200,
        "error_code": None,
        "latency_ms": 80,
        "created_at": created_at,
    }
    return CallRecord(**{**values, **overrides})


def service(
    provider: FakeProvider, ledger: FakeLedger | None = None, cache: FakeCache | None = None
) -> tuple[SearchService, FakeLedger, FakeCache, list[float]]:
    ledger, cache, sleeps = ledger or FakeLedger(), cache or FakeCache(), []
    ports = SearchPorts(provider=provider, ledger=ledger, cache=cache, clock=FixedClock())
    return (
        SearchService(ports, LIMITS, sleep=sleeps.append, jitter=lambda: 0.5),
        ledger,
        cache,
        sleeps,
    )


def outcomes(ledger: FakeLedger) -> list[tuple[SerpCallOutcome, ServedFrom | None]]:
    return [(c.outcome, c.served_from) for c in ledger.calls]


def test_a_local_cache_hit_is_recorded_and_never_calls_serpapi() -> None:
    provider = FakeProvider()
    search, ledger, _, _ = service(provider, cache=FakeCache(**{NEWS.params_hash: PAYLOAD}))
    result = search.search(NEWS, user_id=USER, scan_id=SCAN)
    assert (result.payload, result.served_from, provider.calls) == (
        PAYLOAD,
        ServedFrom.LOCAL_CACHE,
        0,
    )
    assert outcomes(ledger) == [(SerpCallOutcome.SUCCEEDED, ServedFrom.LOCAL_CACHE)]
    assert ledger.calls[0].scan_id == SCAN and ledger.payloads == {}


def test_a_success_is_recorded_kept_and_cached_for_the_engine_ttl() -> None:
    search, ledger, cache, _ = service(FakeProvider(LIVE))
    result = search.search(NEWS, user_id=USER)
    assert outcomes(ledger) == [(SerpCallOutcome.SUCCEEDED, ServedFrom.LIVE)]
    assert (ledger.calls[0].http_status, ledger.calls[0].latency_ms) == (200, 80)
    assert ledger.payloads == {result.call_id: PAYLOAD}
    assert cache.ttls == {NEWS.params_hash: timedelta(hours=3)}


def test_no_cache_skips_the_lookup_but_refreshes_the_cache() -> None:
    fresh = SearchRequest(engine=NEWS.engine, params=NEWS.params, no_cache=True)
    stale = FakeCache(**{fresh.params_hash: {"old": True}})
    search, _, cache, _ = service(FakeProvider(LIVE), cache=stale)
    assert search.search(fresh, user_id=USER).payload == PAYLOAD
    assert cache.entries[fresh.params_hash] == PAYLOAD


def test_transient_failures_are_retried_with_backoff_and_each_attempt_recorded() -> None:
    provider = FakeProvider(failure(SerpErrorCode.TIMEOUT), failure(SerpErrorCode.HTTP_5XX), LIVE)
    search, ledger, _, sleeps = service(provider)
    assert search.search(NEWS, user_id=USER).served_from is ServedFrom.LIVE
    assert [c.error_code for c in ledger.calls] == [
        SerpErrorCode.TIMEOUT,
        SerpErrorCode.HTTP_5XX,
        None,
    ]
    assert ledger.calls[0].latency_ms == 30_000
    assert sleeps == [1.5, 2.5]  # 1 s doubled per retry, plus half a second of jitter


def test_retries_stop_at_the_last_attempt() -> None:
    search, ledger, _, sleeps = service(FakeProvider(*[failure(SerpErrorCode.NETWORK)] * 3))
    with pytest.raises(SearchFailed):
        search.search(NEWS, user_id=USER)
    assert len(ledger.calls) == 3 and len(sleeps) == 2


def test_a_permanent_failure_is_not_retried() -> None:
    search, ledger, _, sleeps = service(FakeProvider(failure(SerpErrorCode.HTTP_4XX)))
    with pytest.raises(SearchFailed) as exc:
        search.search(NEWS, user_id=USER)
    assert exc.value.code is SerpErrorCode.HTTP_4XX
    assert (len(ledger.calls), sleeps) == (1, [])


def test_an_open_breaker_skips_without_calling() -> None:
    ledger = FakeLedger()
    timeout = {"outcome": SerpCallOutcome.FAILED, "served_from": None, "http_status": None}
    for minutes in range(5):
        at = NOW - timedelta(minutes=minutes)
        ledger.calls.append(live_call(at, error_code=SerpErrorCode.TIMEOUT, **timeout))
    provider = FakeProvider()
    search, _, _, _ = service(provider, ledger=ledger)
    with pytest.raises(SearchSkipped) as exc:
        search.search(NEWS, user_id=USER)
    assert (exc.value.outcome, exc.value.reason, provider.calls) == (
        SerpCallOutcome.CIRCUIT_OPEN,
        "circuit_open",
        0,
    )
    assert outcomes(ledger)[-1] == (SerpCallOutcome.CIRCUIT_OPEN, None)


@pytest.mark.parametrize(
    ("history", "budget", "scan_id", "reason"),
    [
        ([live_call(NOW, user_id=uuid.uuid4())] * 100, None, None, "global_daily_cap"),
        ([live_call(NOW)] * 10, None, None, "monthly_budget"),  # the default budget
        ([live_call(NOW)] * 2, 2, None, "monthly_budget"),  # the user's own budget
        ([live_call(NOW, scan_id=SCAN)] * 5, None, SCAN, "scan_budget"),
    ],
)
def test_budgets_skip_the_call(
    history: list[CallRecord], budget: int | None, scan_id: uuid.UUID | None, reason: str
) -> None:
    ledger = FakeLedger(budget=budget)
    ledger.calls.extend(history)
    search, _, _, _ = service(FakeProvider(), ledger=ledger)
    with pytest.raises(SearchSkipped) as exc:
        search.search(NEWS, user_id=USER, scan_id=scan_id)
    assert (exc.value.outcome, exc.value.reason) == (SerpCallOutcome.SKIPPED_BUDGET, reason)
    assert outcomes(ledger)[-1] == (SerpCallOutcome.SKIPPED_BUDGET, None)


def test_budgets_reset_at_utc_day_and_month_boundaries() -> None:
    ledger = FakeLedger()
    yesterday = NOW.replace(hour=0) - timedelta(seconds=1)
    last_month = NOW.replace(day=1, hour=0) - timedelta(seconds=1)
    ledger.calls.extend([live_call(yesterday, user_id=uuid.uuid4())] * 100)
    ledger.calls.extend([live_call(last_month)] * 10)
    search, _, _, _ = service(FakeProvider(LIVE), ledger=ledger)
    assert search.search(NEWS, user_id=USER).served_from is ServedFrom.LIVE


def test_backoff_is_capped() -> None:
    limits = SearchLimits(monthly_default=10, global_daily=100, per_scan=5, attempts=6)
    sleeps: list[float] = []
    ports = SearchPorts(
        provider=FakeProvider(*[failure(SerpErrorCode.TIMEOUT)] * 6),
        ledger=FakeLedger(),
        cache=FakeCache(),
        clock=FixedClock(),
    )
    with pytest.raises(SearchFailed):
        SearchService(ports, limits, sleep=sleeps.append, jitter=lambda: 0.0).search(
            NEWS, user_id=USER
        )
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 8.0]


class RejectingLedger(FakeLedger):
    def store_payload(
        self, call_id: uuid.UUID, payload: Mapping[str, Any], *, at: datetime
    ) -> None:
        raise RuntimeError("payload rejected")


def test_a_rejected_payload_propagates_after_the_call_is_recorded() -> None:
    ledger = RejectingLedger()
    search, _, cache, _ = service(FakeProvider(LIVE), ledger=ledger)
    with pytest.raises(RuntimeError, match="payload rejected"):
        search.search(NEWS, user_id=USER)
    assert outcomes(ledger) == [(SerpCallOutcome.SUCCEEDED, ServedFrom.LIVE)]
    assert cache.entries == {}  # never cached either


@pytest.mark.parametrize(
    "limits",
    [{"attempts": 0}, {"cache_ttl": {SerpEngine.GOOGLE: timedelta(hours=1)}}],
)
def test_limits_are_checked(limits: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SearchLimits(monthly_default=10, global_daily=100, per_scan=5, **limits)


def open_breaker(ledger: FakeLedger) -> None:
    timeout = {"outcome": SerpCallOutcome.FAILED, "served_from": None, "http_status": None}
    for minutes in range(5):
        at = NOW - timedelta(minutes=minutes)
        ledger.calls.append(live_call(at, error_code=SerpErrorCode.TIMEOUT, **timeout))


def test_a_cache_hit_is_served_even_with_the_breaker_open() -> None:
    ledger = FakeLedger()
    open_breaker(ledger)
    search, _, _, _ = service(
        FakeProvider(), ledger=ledger, cache=FakeCache(**{NEWS.params_hash: PAYLOAD})
    )
    assert search.search(NEWS, user_id=USER).served_from is ServedFrom.LOCAL_CACHE
    assert SerpCallOutcome.CIRCUIT_OPEN not in [c.outcome for c in ledger.calls]


def test_the_breaker_is_checked_before_budgets() -> None:
    ledger = FakeLedger(budget=0)
    open_breaker(ledger)
    search, _, _, _ = service(FakeProvider(), ledger=ledger)
    with pytest.raises(SearchSkipped) as exc:
        search.search(NEWS, user_id=USER)
    assert exc.value.reason is SkipReason.CIRCUIT_OPEN


def test_only_this_scans_calls_count_against_its_cap() -> None:
    ledger = FakeLedger()
    ledger.calls.extend([live_call(NOW)] * 4 + [live_call(NOW, scan_id=uuid.uuid4())] * 4)
    search, _, _, _ = service(FakeProvider(LIVE), ledger=ledger)
    assert search.search(NEWS, user_id=USER, scan_id=SCAN).served_from is ServedFrom.LIVE


def test_usage_periods_are_utc_and_need_a_timezone() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    early_ist = datetime(2026, 10, 1, 2, 0, tzinfo=ist)  # still 30 September in UTC
    assert day_start(early_ist) == datetime(2026, 9, 30, tzinfo=UTC)
    assert month_start(early_ist) == datetime(2026, 9, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        day_start(datetime(2026, 10, 1))  # noqa: DTZ001  # a naive time is refused
