"""The SerpApi ledger adapter on real Postgres (data-model §5)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, select
from sqlalchemy.exc import IntegrityError

from serpsense.adapters.db.search_ledger import SqlSearchLedger
from serpsense.domain.enums import SerpCallOutcome, SerpEngine, SerpErrorCode, ServedFrom
from serpsense.ports.search_ledger import CallRecord
from serpsense.ports.search_provider import SearchRequest
from tests.integration.db_helpers import NOW, add, add_brand, add_scan, add_user, table, violation

pytestmark = pytest.mark.integration

CALLS = table("serp_calls")
NEWS = SearchRequest(engine=SerpEngine.GOOGLE_NEWS, params={"q": "Ola", "num": 10})


@pytest.fixture
def ledger(conn: Connection) -> SqlSearchLedger:
    @contextmanager
    def savepoint() -> Iterator[Connection]:
        with conn.begin_nested():
            yield conn

    return SqlSearchLedger(savepoint)


def call(user_id: uuid.UUID, **overrides: Any) -> CallRecord:
    values: dict[str, Any] = {
        "user_id": user_id,
        "scan_id": None,
        "request": NEWS,
        "outcome": SerpCallOutcome.SUCCEEDED,
        "served_from": ServedFrom.LIVE,
        "http_status": 200,
        "error_code": None,
        "latency_ms": 800,
        "created_at": NOW,
    }
    return CallRecord(**{**values, **overrides})


def failed(user_id: uuid.UUID, code: SerpErrorCode, **overrides: Any) -> CallRecord:
    status = None if code in {SerpErrorCode.NETWORK, SerpErrorCode.TIMEOUT} else 503
    return call(
        user_id,
        outcome=SerpCallOutcome.FAILED,
        served_from=None,
        http_status=status,
        error_code=code,
        **overrides,
    )


def test_a_call_is_recorded_with_its_canonical_params(
    conn: Connection, ledger: SqlSearchLedger
) -> None:
    owner = add_user(conn)
    scan_id = add_scan(conn, add_brand(conn, owner))
    call_id = ledger.record_call(call(owner, scan_id=scan_id))
    row = conn.execute(select(CALLS).where(CALLS.c.id == call_id)).one()
    assert (row.scan_id, row.params, row.params_hash) == (
        scan_id,
        {"q": "Ola", "num": "10"},
        NEWS.params_hash,
    )
    failure_id = ledger.record_call(failed(owner, SerpErrorCode.HTTP_5XX))
    error = conn.execute(select(CALLS.c.error_code).where(CALLS.c.id == failure_id)).scalar_one()
    assert error == "serpapi.http_5xx"


def test_a_rejected_payload_leaves_the_call_recorded(
    conn: Connection, ledger: SqlSearchLedger
) -> None:
    owner = add_user(conn)
    ok_id = ledger.record_call(call(owner))
    ledger.store_payload(ok_id, {"news_results": []}, at=NOW)
    failed_id = ledger.record_call(failed(owner, SerpErrorCode.TIMEOUT))
    with pytest.raises(IntegrityError) as exc:
        ledger.store_payload(failed_id, {"error": "x"}, at=NOW)
    assert violation(exc).constraint_name == "ck_raw_responses_call_succeeded"
    assert conn.execute(select(CALLS.c.id).where(CALLS.c.id == failed_id)).scalar_one() == failed_id


def test_recent_attempts_are_what_reached_serpapi_newest_first(
    conn: Connection, ledger: SqlSearchLedger
) -> None:
    owner = add_user(conn)
    minute = timedelta(minutes=1)
    ledger.record_call(
        failed(owner, SerpErrorCode.TIMEOUT, created_at=NOW - 20 * minute)
    )  # too old
    ledger.record_call(failed(owner, SerpErrorCode.NETWORK, created_at=NOW - 3 * minute))
    ledger.record_call(call(owner, created_at=NOW - 2 * minute))
    ledger.record_call(
        call(owner, served_from=ServedFrom.LOCAL_CACHE, http_status=None, created_at=NOW)
    )
    skipped = {"outcome": SerpCallOutcome.CIRCUIT_OPEN, "served_from": None, "http_status": None}
    ledger.record_call(call(owner, latency_ms=0, created_at=NOW, **skipped))
    other = SearchRequest(engine=SerpEngine.GOOGLE, params={"q": "Ola"})
    ledger.record_call(failed(owner, SerpErrorCode.TIMEOUT, request=other, created_at=NOW))
    ledger.record_call(failed(owner, SerpErrorCode.HTTP_5XX, created_at=NOW - minute))
    over_budget = {**skipped, "outcome": SerpCallOutcome.SKIPPED_BUDGET}
    ledger.record_call(call(owner, latency_ms=0, created_at=NOW - minute, **over_budget))
    ledger.record_call(
        call(
            owner, served_from=ServedFrom.SERPAPI_CACHE, created_at=NOW - 30 * timedelta(seconds=1)
        )
    )
    retired = {"outcome": "failed", "error_code": "serpapi.retired_code", "latency_ms": 1}
    news = {"engine": "google_news", "params_hash": NEWS.params_hash, "params": {}}
    add(conn, CALLS, user_id=owner, created_at=NOW - 4 * minute, **news, **retired)
    attempts = ledger.recent_attempts(SerpEngine.GOOGLE_NEWS, since=NOW - 15 * minute, limit=6)
    assert attempts == [
        None,  # served from SerpApi's cache: it reached SerpApi
        SerpErrorCode.HTTP_5XX,
        None,
        SerpErrorCode.NETWORK,
        SerpErrorCode.SEARCH_ERROR,  # a code the domain no longer knows is a permanent failure
    ]
    assert ledger.recent_attempts(SerpEngine.GOOGLE_NEWS, since=NOW - 15 * minute, limit=1) == [
        None
    ]


def test_live_calls_count_only_billable_calls(conn: Connection, ledger: SqlSearchLedger) -> None:
    owner, other = add_user(conn), add_user(conn, "other@example.com")
    scan_id = add_scan(conn, add_brand(conn, owner))
    ledger.record_call(call(owner, scan_id=scan_id))
    ledger.record_call(call(owner))  # a Preview call
    ledger.record_call(call(owner, served_from=ServedFrom.SERPAPI_CACHE))
    ledger.record_call(failed(owner, SerpErrorCode.HTTP_5XX))
    ledger.record_call(call(other, created_at=NOW - timedelta(days=40)))
    ledger.record_call(call(other))
    since = NOW - timedelta(days=30)
    assert ledger.live_calls(since=since) == 3
    assert ledger.live_calls(since=since, user_id=owner) == 2
    assert ledger.live_calls(since=since, scan_id=scan_id) == 1
    assert ledger.live_calls(since=NOW + timedelta(seconds=1)) == 0


def test_monthly_budget_is_the_latest_in_force(conn: Connection, ledger: SqlSearchLedger) -> None:
    owner = add_user(conn)
    assert ledger.monthly_budget(owner, at=NOW) is None
    budgets = table("user_search_budgets")
    for searches, starts in [
        (1500, NOW - timedelta(days=10)),
        (200, NOW),
        (900, NOW + timedelta(days=1)),
    ]:
        add(
            conn,
            budgets,
            user_id=owner,
            monthly_searches=searches,
            effective_from=starts,
            created_at=NOW,
        )
    assert ledger.monthly_budget(owner, at=NOW) == 200
    assert ledger.monthly_budget(owner, at=NOW - timedelta(days=1)) == 1500


@pytest.mark.parametrize(
    "overrides",
    [
        {"served_from": None},  # a success has a source
        {"outcome": SerpCallOutcome.FAILED},  # a failure has no source and needs a code
        {"error_code": SerpErrorCode.TIMEOUT},  # only failures carry a code
        {"outcome": SerpCallOutcome.SKIPPED_BUDGET, "served_from": None},  # skipped: no status
        {"latency_ms": -1},
    ],
)
def test_a_record_the_ledger_would_reject_is_refused_first(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        call(uuid.uuid4(), **overrides)


def test_replay_reads_how_often_a_request_was_answered(
    conn: Connection, ledger: SqlSearchLedger
) -> None:
    owner = add_user(conn)
    assert ledger.times_answered(NEWS) == 0
    ledger.record_call(call(owner))
    ledger.record_call(call(owner, served_from=ServedFrom.SERPAPI_CACHE))
    ledger.record_call(failed(owner, SerpErrorCode.HTTP_5XX))  # failures don't count
    other = SearchRequest(engine=SerpEngine.GOOGLE_NEWS, params={"q": "Uber", "num": 10})
    ledger.record_call(call(owner, request=other))
    assert (ledger.times_answered(NEWS), ledger.times_answered(other)) == (2, 1)
