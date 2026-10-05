"""The LLM call ledger on real Postgres: one row per call, and the user's spend for budgets."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import Connection, select

from serpsense.adapters.db.llm_ledger import SqlLlmLedger
from serpsense.domain.enums import LlmCallOutcome, LlmTask
from serpsense.domain.llm_pricing import TokenUsage
from serpsense.ports.llm_ledger import LlmCallRecord, LlmLedger
from tests.integration.db_helpers import NOW, add_brand, add_scan, add_user, table

pytestmark = pytest.mark.integration

CALLS = table("llm_calls")
USAGE = TokenUsage(input=5200, output=900, cache_read=4000, cache_write=0)


@pytest.fixture
def ledger(conn: Connection) -> LlmLedger:
    @contextmanager
    def savepoint() -> Iterator[Connection]:
        with conn.begin_nested():
            yield conn

    return SqlLlmLedger(savepoint)


def call(user_id: uuid.UUID, **overrides: object) -> LlmCallRecord:
    values: dict[str, object] = {
        "user_id": user_id,
        "scan_id": None,
        "task": LlmTask.LABEL_MENTIONS,
        "requested_model": "claude-opus-5-5",
        "served_model": "claude-opus-5-5",
        "prompt_version": "label_mentions/v1",
        "request_settings": {"effort": "low", "max_tokens": 8000},
        "usage": USAGE,
        "cost_micros": 39_600,
        "currency": "USD",
        "stop_reason": "end_turn",
        "outcome": LlmCallOutcome.SUCCEEDED,
        "latency_ms": 3100,
        "created_at": NOW,
    }
    return LlmCallRecord(**{**values, **overrides})  # type: ignore[arg-type]  # test builder


def test_a_call_is_recorded_with_its_counts_cost_and_settings(
    conn: Connection, ledger: LlmLedger
) -> None:
    owner = add_user(conn)
    scan_id = add_scan(conn, add_brand(conn, owner))
    call_id = ledger.record(call(owner, scan_id=scan_id))
    row = conn.execute(select(CALLS).where(CALLS.c.id == call_id)).one()
    assert (row.scan_id, row.input_tokens, row.cache_read_tokens, row.cost_micros) == (
        scan_id,
        5200,
        4000,
        39_600,
    )
    assert (row.request_settings, row.outcome) == (
        {"effort": "low", "max_tokens": 8000},
        "succeeded",
    )


def test_spend_counts_the_users_calls_since_a_time(conn: Connection, ledger: LlmLedger) -> None:
    owner, other = add_user(conn), add_user(conn, "other@example.com")
    ledger.record(call(owner, created_at=NOW - timedelta(days=40), cost_micros=999_999))
    ledger.record(call(owner))
    failed = {"served_model": None, "stop_reason": None, "outcome": LlmCallOutcome.FAILED}
    ledger.record(call(owner, cost_micros=0, usage=replace(USAGE, input=0, output=0), **failed))
    ledger.record(call(other))
    assert ledger.spent_since(owner, NOW - timedelta(days=1)) == 39_600
    assert ledger.spent_since(add_user(conn, "new@example.com"), NOW) == 0
    assert ledger.spent_in_all_since(NOW - timedelta(days=1)) == 2 * 39_600  # both users'
    assert ledger.spent_in_all_since(NOW + timedelta(seconds=1)) == 0
