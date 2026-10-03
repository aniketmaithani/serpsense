"""SerpApi ledger and raw response rules enforced by Postgres itself (data-model.md §5)."""

import hashlib
import uuid
from enum import StrEnum
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, insert, text, update
from sqlalchemy.exc import IntegrityError

from serpsense.domain.enums import SerpCallOutcome, SerpEngine, ServedFrom
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

CALLS = table("serp_calls")
RAW = table("raw_responses")
PARAMS_HASH = hashlib.sha256(b'{"engine":"google_news","q":"voltbox"}').hexdigest()
FAILED: dict[str, Any] = {"served_from": None, "outcome": "failed"}
UNSUCCESSFUL: list[dict[str, Any]] = [
    {**FAILED, "error_code": "serpapi.timeout"},
    {"served_from": None, "outcome": "circuit_open", "http_status": None, "latency_ms": 0},
]


def add_call(conn: Connection, user_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "scan_id": None,
        "engine": "google_news",
        "params_hash": PARAMS_HASH,
        "params": {"q": "voltbox", "gl": "in", "hl": "hi"},
        "served_from": "live",
        "outcome": "succeeded",
        "http_status": 200,
        "latency_ms": 840,
        "created_at": NOW,
    }
    return add(conn, CALLS, user_id=user_id, **{**values, **overrides})


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # a Preview call outside any scan
        {"params": {"q": "voltbox api_key leak"}},  # words in user text are not the key
        {"served_from": "local_cache", "http_status": None},
        {"served_from": None, "outcome": "skipped_budget", "http_status": None, "latency_ms": 0},
        {"served_from": None, "outcome": "circuit_open", "http_status": None, "latency_ms": 0},
        {**FAILED, "http_status": 429, "error_code": "serpapi.http_429"},
    ],
)
def test_valid_calls_are_accepted(conn: Connection, overrides: dict[str, Any]) -> None:
    add_call(conn, add_user(conn), **overrides)


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"outcome": "skipped_budget"}, "served_from_iff_succeeded"),
        ({"outcome": "failed", "error_code": "serpapi.timeout"}, "served_from_iff_succeeded"),
        ({"served_from": None}, "served_from_iff_succeeded"),
        (FAILED, "error_code_iff_failed"),
        ({"error_code": "serpapi.http_429"}, "error_code_iff_failed"),
        ({**FAILED, "error_code": "Rate limited"}, "error_code_format"),
        ({"http_status": 99}, "http_status_range"),
        ({"http_status": 600}, "http_status_range"),
        ({"latency_ms": -1}, "latency_non_negative"),
        ({"served_from": None, "outcome": "skipped_budget", "latency_ms": 0}, "skipped_not_sent"),
        ({"served_from": None, "outcome": "circuit_open", "http_status": None}, "skipped_not_sent"),
        ({"params_hash": "not-a-hash"}, "params_hash_sha256"),
        ({"params_hash": PARAMS_HASH.upper()}, "params_hash_sha256"),
        ({"params": ["voltbox"]}, "params_is_object"),
        ({"params": {"q": "voltbox", "api_key": "x"}}, "params_no_api_key"),
        ({"params": {"q": "voltbox", "x": [{"api_key": "x"}]}}, "params_no_api_key"),
        ({"params": {"next": "https://serpapi.com/search?api_key=x"}}, "params_no_api_key"),
    ],
)
def test_call_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_call(conn, add_user(conn), **overrides)
    assert violation(exc).constraint_name == f"ck_serp_calls_{check}"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_ledger_is_append_only(conn: Connection, mutation: str) -> None:
    call_id = add_call(conn, add_user(conn))
    statements: dict[str, Executable] = {
        "update": update(CALLS).where(CALLS.c.id == call_id).values(latency_ms=1),
        "delete": delete(CALLS).where(CALLS.c.id == call_id),
        "truncate": text("TRUNCATE serp_calls CASCADE"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


def test_raw_responses_are_one_per_call_and_prunable(conn: Connection) -> None:
    call_id = add_call(conn, add_user(conn))
    raw_id = add(conn, RAW, serp_call_id=call_id, payload={"q": "api_key leak"}, created_at=NOW)
    with pytest.raises(IntegrityError) as exc, conn.begin_nested():
        add(conn, RAW, serp_call_id=call_id, payload={"a": 2}, created_at=NOW)
    assert violation(exc).constraint_name == "uq_raw_responses_serp_call_id"
    conn.execute(update(RAW).where(RAW.c.id == raw_id).values(payload={"pruned": True}))
    conn.execute(delete(RAW).where(RAW.c.id == raw_id))  # retention may delete payloads


@pytest.mark.parametrize(
    ("payload", "check"),
    [
        (["x"], "payload_is_object"),
        ({"serpapi_pagination": {"next": "https://serpapi.com/x?api_key=k"}}, "payload_no_api_key"),
        ({"search_parameters": {"api_key": "k"}}, "payload_no_api_key"),
    ],
)
def test_raw_payload_checks(conn: Connection, payload: Any, check: str) -> None:
    call_id = add_call(conn, add_user(conn))
    with pytest.raises(IntegrityError) as exc:
        add(conn, RAW, serp_call_id=call_id, payload=payload, created_at=NOW)
    assert violation(exc).constraint_name == f"ck_raw_responses_{check}"


def test_scan_calls_belong_to_the_brand_owner(conn: Connection) -> None:
    owner = add_user(conn)
    scan_id = add_scan(conn, add_brand(conn, owner))
    add_call(conn, owner, scan_id=scan_id)
    with pytest.raises(IntegrityError) as exc:
        add_call(conn, add_user(conn, "other@example.com"), scan_id=scan_id)
    assert violation(exc).constraint_name == "ck_serp_calls_user_owns_scan"


@pytest.mark.parametrize("missing", ["scan", "call"])
def test_missing_scan_or_call_reports_the_foreign_key(conn: Connection, missing: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        if missing == "scan":
            add_call(conn, add_user(conn), scan_id=uuid.uuid4())
        else:
            add(conn, RAW, serp_call_id=uuid.uuid4(), payload={}, created_at=NOW)
    expected = {
        "scan": "fk_serp_calls_scan_id_scans",
        "call": "fk_raw_responses_serp_call_id_serp_calls",
    }
    assert violation(exc).constraint_name == expected[missing]


@pytest.mark.parametrize("via", ["insert", "update"])
@pytest.mark.parametrize("overrides", UNSUCCESSFUL)
def test_raw_responses_only_for_successful_calls(
    conn: Connection, via: str, overrides: dict[str, Any]
) -> None:
    owner = add_user(conn)
    raw_id = add(conn, RAW, serp_call_id=add_call(conn, owner), payload={}, created_at=NOW)
    call_id = add_call(conn, owner, **overrides)
    statements: dict[str, Executable] = {
        "insert": insert(RAW).values(
            id=uuid.uuid4(), serp_call_id=call_id, payload={}, created_at=NOW
        ),
        "update": update(RAW).where(RAW.c.id == raw_id).values(serp_call_id=call_id),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[via])
    assert violation(exc).constraint_name == "ck_raw_responses_call_succeeded"


@pytest.mark.parametrize("target", ["scans", "users"])
def test_referenced_scan_and_user_cannot_be_deleted(conn: Connection, target: str) -> None:
    owner = add_user(conn)
    scan_id = add_scan(conn, add_brand(conn, owner))
    add_call(conn, owner, scan_id=scan_id)
    caller = add_user(conn, "caller@example.com")  # owns nothing, so only its Preview call refers
    add_call(conn, caller)
    victim, column = (scan_id, "scan_id") if target == "scans" else (caller, "user_id")
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table(target)).where(table(target).c.id == victim))
    assert violation(exc).constraint_name == f"fk_serp_calls_{column}_{target}"


@pytest.mark.parametrize(
    ("pg_type", "enum"),
    [
        ("serp_engine", SerpEngine),
        ("served_from", ServedFrom),
        ("serp_call_outcome", SerpCallOutcome),
    ],
)
def test_ledger_enums_match_domain(conn: Connection, pg_type: str, enum: type[StrEnum]) -> None:
    labels = conn.execute(text(f"SELECT unnest(enum_range(NULL::{pg_type}))::text")).scalars()
    assert list(labels) == [member.value for member in enum]
