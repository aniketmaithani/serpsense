"""Per-surface result rules enforced by Postgres itself (data-model.md §4)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    RESTRICT_VIOLATION,
    add_brand,
    add_scan,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

RESULTS = table("scan_surface_results")


def scan(conn: Connection) -> uuid.UUID:
    return add_scan(conn, add_brand(conn, add_user(conn)))


def add_result(conn: Connection, scan_id: uuid.UUID, **overrides: Any) -> None:
    values = {"surface": "news", "outcome": "succeeded", "error_code": None, **overrides}
    conn.execute(insert(RESULTS).values(scan_id=scan_id, **values))


def test_results_round_trip(conn: Connection) -> None:
    scan_id = scan(conn)
    add_result(conn, scan_id, surface="news")
    add_result(conn, scan_id, surface="play", outcome="failed", error_code="serpapi.http_429")
    add_result(conn, scan_id, surface="ai_overview", outcome="not_shown")
    rows = conn.execute(
        select(RESULTS.c.surface, RESULTS.c.outcome, RESULTS.c.error_code)
        .where(RESULTS.c.scan_id == scan_id)
        .order_by(RESULTS.c.surface)
    ).all()
    assert [tuple(row) for row in rows] == [
        ("ai_overview", "not_shown", None),
        ("news", "succeeded", None),
        ("play", "failed", "serpapi.http_429"),
    ]


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"outcome": "failed"}, "error_code_iff_failed"),
        ({"outcome": "succeeded", "error_code": "http_500"}, "error_code_iff_failed"),
        ({"outcome": "circuit_open", "error_code": "http_500"}, "error_code_iff_failed"),
        ({"outcome": "failed", "error_code": "HTTP 500"}, "error_code_format"),
        ({"outcome": "failed", "error_code": ""}, "error_code_format"),
        ({"outcome": "failed", "error_code": "e" * 65}, "error_code_format"),
    ],
)
def test_result_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_result(conn, scan(conn), **overrides)
    assert violation(exc).constraint_name == f"ck_scan_surface_results_{check}"


def test_error_code_accepts_64_characters(conn: Connection) -> None:
    add_result(conn, scan(conn), outcome="failed", error_code="e" * 64)


def test_one_result_per_surface_per_scan(conn: Connection) -> None:
    scan_id = scan(conn)
    add_result(conn, scan_id, surface="news")
    with pytest.raises(IntegrityError) as exc:
        add_result(conn, scan_id, surface="news", outcome="not_shown")
    assert violation(exc).constraint_name == "pk_scan_surface_results"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_results_are_append_only(conn: Connection, mutation: str) -> None:
    scan_id = scan(conn)
    add_result(conn, scan_id)
    statements: dict[str, Executable] = {
        "update": update(RESULTS).where(RESULTS.c.scan_id == scan_id).values(outcome="disabled"),
        "delete": delete(RESULTS).where(RESULTS.c.scan_id == scan_id),
        "truncate": text("TRUNCATE scan_surface_results"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


def test_scan_with_results_cannot_be_deleted(conn: Connection) -> None:
    scan_id = scan(conn)
    add_result(conn, scan_id)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table("scans")).where(table("scans").c.id == scan_id))
    assert violation(exc).constraint_name == "fk_scan_surface_results_scan_id_scans"
