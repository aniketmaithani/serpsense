"""LLM call ledger rules enforced by Postgres itself (data-model.md §6)."""

import uuid
from enum import StrEnum
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, text, update
from sqlalchemy.exc import IntegrityError

from serpsense.domain.enums import LlmCallOutcome, LlmTask
from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add,
    add_brand,
    add_scan,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

CALLS = table("llm_calls")
FAILED: dict[str, Any] = {"outcome": "failed", "served_model": None, "stop_reason": None}


def add_call(conn: Connection, user_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "scan_id": None,
        "task": "label_mentions",
        "requested_model": "claude-opus-5-5",
        "served_model": "claude-opus-5-5",
        "prompt_version": "label_mentions/v1",
        "request_settings": {"effort": "low", "max_tokens": 4096},
        "input_tokens": 5200,
        "output_tokens": 900,
        "cache_read_tokens": 4000,
        "cache_write_tokens": 0,
        "cost_micros": 41_250,
        "currency": "USD",
        "stop_reason": "end_turn",
        "outcome": "succeeded",
        "latency_ms": 3100,
        "created_at": NOW,
    }
    return add(conn, CALLS, user_id=user_id, **{**values, **overrides})


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # a call outside any scan, such as a draft
        {"outcome": "refused", "stop_reason": "refusal", "served_model": "claude-sonnet-5-5"},
        {"outcome": "truncated", "stop_reason": "max_tokens"},
        {"outcome": "invalid_output"},
        {**FAILED, "input_tokens": 0, "output_tokens": 0, "cost_micros": 0},
    ],
)
def test_valid_calls_are_accepted(conn: Connection, overrides: dict[str, Any]) -> None:
    add_call(conn, add_user(conn), **overrides)


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"outcome": "failed"}, "served_model_iff_response"),
        ({"served_model": None}, "served_model_iff_response"),
        ({"requested_model": "Claude Opus"}, "requested_model_format"),
        ({"served_model": ""}, "served_model_format"),
        ({"prompt_version": "label_mentions/1"}, "prompt_version_format"),
        ({"prompt_version": "label_mentions/v0"}, "prompt_version_format"),
        ({"prompt_version": "draft_response/v1"}, "prompt_matches_task"),
        ({"stop_reason": "End Turn"}, "stop_reason_format"),
        ({"request_settings": ["low"]}, "request_settings_is_object"),
        ({"input_tokens": -1}, "input_tokens_non_negative"),
        ({"cache_write_tokens": -1}, "cache_write_tokens_non_negative"),
        ({"cost_micros": -1}, "cost_micros_non_negative"),
        ({"currency": "usd"}, "currency_iso_4217"),
        ({"latency_ms": -1}, "latency_non_negative"),
    ],
)
def test_call_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_call(conn, add_user(conn), **overrides)
    assert violation(exc).constraint_name == f"ck_llm_calls_{check}"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_ledger_is_append_only(conn: Connection, mutation: str) -> None:
    call_id = add_call(conn, add_user(conn))
    statements: dict[str, Executable] = {
        "update": update(CALLS).where(CALLS.c.id == call_id).values(cost_micros=0),
        "delete": delete(CALLS).where(CALLS.c.id == call_id),
        "truncate": text("TRUNCATE llm_calls CASCADE"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


def test_scan_calls_belong_to_the_brand_owner(conn: Connection) -> None:
    owner = add_user(conn)
    scan_id = add_scan(conn, add_brand(conn, owner))
    add_call(conn, owner, scan_id=scan_id)
    with pytest.raises(IntegrityError) as exc:
        add_call(conn, add_user(conn, "other@example.com"), scan_id=scan_id)
    assert violation(exc).constraint_name == "ck_llm_calls_user_owns_scan"


@pytest.mark.parametrize("target", ["scans", "users"])
def test_referenced_scan_and_user_cannot_be_deleted(conn: Connection, target: str) -> None:
    owner = add_user(conn)
    scan_id = add_scan(conn, add_brand(conn, owner))
    add_call(conn, owner, scan_id=scan_id)
    caller = add_user(conn, "caller@example.com")  # owns nothing; only its own call refers
    add_call(conn, caller)
    victim, column = (scan_id, "scan_id") if target == "scans" else (caller, "user_id")
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table(target)).where(table(target).c.id == victim))
    assert violation(exc).constraint_name == f"fk_llm_calls_{column}_{target}"


@pytest.mark.parametrize(
    ("pg_type", "enum"), [("llm_task", LlmTask), ("llm_call_outcome", LlmCallOutcome)]
)
def test_ledger_enums_match_domain(conn: Connection, pg_type: str, enum: type[StrEnum]) -> None:
    labels = conn.execute(text(f"SELECT unnest(enum_range(NULL::{pg_type}))::text")).scalars()
    assert list(labels) == [member.value for member in enum]
