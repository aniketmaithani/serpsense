"""Scan rules enforced by Postgres itself (data-model.md §4)."""

import uuid
from datetime import timedelta
from enum import StrEnum
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, delete, select, text, update
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from serpsense.domain.enums import (
    ScanStatus,
    ScanTrigger,
    Surface,
    SurfaceOutcome,
    TransitionActor,
)
from tests.integration.db_helpers import NOW, add, add_brand, add_user, table, violation

pytestmark = pytest.mark.integration

LOCK_NOT_AVAILABLE = "55P03"
SCANS = table("scans")
TERMINAL = ["succeeded", "partial", "failed", "skipped"]


def add_scan(conn: Connection, brand_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "trigger": "schedule",
        "scheduled_for": NOW,
        "status": "queued",
        "settings_snapshot": {"preset": "standard"},
        "estimated_searches": 14,
        "created_at": NOW,
    }
    return add(conn, SCANS, brand_id=brand_id, **{**values, **overrides})


def brand(conn: Connection) -> uuid.UUID:
    return add_brand(conn, add_user(conn))


def test_scan_round_trips(conn: Connection) -> None:
    scan_id = add_scan(conn, brand(conn))
    row = conn.execute(select(SCANS).where(SCANS.c.id == scan_id)).one()
    assert (row.trigger, row.status, row.scheduled_for, row.settings_snapshot) == (
        "schedule",
        "queued",
        NOW,
        {"preset": "standard"},
    )


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"scheduled_for": None}, "scheduled_for_schedule"),
        ({"trigger": "replay"}, "scheduled_for_schedule"),
        ({"trigger": "manual", "scheduled_for": None}, "requested_by_manual"),
        ({"requested_by": "owner"}, "requested_by_manual"),
        (
            {"trigger": "replay", "scheduled_for": None, "requested_by": "owner"},
            "requested_by_manual",
        ),
        ({"settings_snapshot": ["standard"]}, "settings_snapshot_is_object"),
        ({"estimated_searches": -1}, "estimated_searches_non_negative"),
    ],
)
def test_scan_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    owner = add_user(conn)
    if overrides.get("requested_by") == "owner":
        overrides = {**overrides, "requested_by": owner}
    with pytest.raises(IntegrityError) as exc:
        add_scan(conn, add_brand(conn, owner), **overrides)
    assert violation(exc).constraint_name == f"ck_scans_{check}"


def test_manual_and_replay_scans_are_accepted(conn: Connection) -> None:
    owner = add_user(conn)
    brand_id = add_brand(conn, owner)
    add_scan(conn, brand_id, trigger="manual", scheduled_for=None, requested_by=owner)
    add_scan(conn, add_brand(conn, owner, slug="soundnest"), trigger="replay", scheduled_for=None)


def test_finished_manual_scans_share_a_brand(conn: Connection) -> None:
    """NULL slots are distinct, so manual scans never collide on the slot constraint."""
    owner = add_user(conn)
    brand_id = add_brand(conn, owner)
    manual = {"trigger": "manual", "scheduled_for": None, "requested_by": owner}
    add_scan(conn, brand_id, status="succeeded", **manual)
    add_scan(conn, brand_id, status="failed", **manual)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("brand_id", "other_brand"),
        ("scheduled_for", NOW + timedelta(hours=1)),
        ("settings_snapshot", {"preset": "deep"}),
        ("estimated_searches", 99),
        ("requested_by", "requester"),  # NULL -> value must be caught too
    ],
)
def test_only_status_can_change(conn: Connection, column: str, value: Any) -> None:
    scan_id = add_scan(conn, brand(conn))
    if value == "other_brand":
        value = add_brand(conn, add_user(conn, "other@example.com"))
    if value == "requester":
        value = add_user(conn, "requester@example.com")
    conn.execute(update(SCANS).where(SCANS.c.id == scan_id).values(status="running"))
    with pytest.raises(IntegrityError) as exc:
        conn.execute(update(SCANS).where(SCANS.c.id == scan_id).values({column: value}))
    assert violation(exc).constraint_name == "ck_scans_identity_immutable"


def test_one_scan_per_brand_per_slot(conn: Connection) -> None:
    brand_id = brand(conn)
    add_scan(conn, brand_id, status="succeeded")
    add_scan(conn, add_brand(conn, add_user(conn, "b@example.com")))  # same slot, other brand
    with pytest.raises(IntegrityError) as exc:
        add_scan(conn, brand_id, status="succeeded")
    assert violation(exc).constraint_name == "uq_scans_brand_id_scheduled_for"


@pytest.mark.parametrize("active", ["queued", "running"])
def test_second_active_scan_is_rejected(conn: Connection, active: str) -> None:
    brand_id = brand(conn)
    add_scan(conn, brand_id, status=active)
    with pytest.raises(IntegrityError) as exc:
        add_scan(conn, brand_id, scheduled_for=NOW + timedelta(hours=6))
    assert violation(exc).constraint_name == "uq_scans_brand_id_active"


@pytest.mark.parametrize("finished", TERMINAL)
def test_new_scan_allowed_once_previous_finished(conn: Connection, finished: str) -> None:
    brand_id = brand(conn)
    first = add_scan(conn, brand_id)
    conn.execute(update(SCANS).where(SCANS.c.id == first).values(status=finished))
    add_scan(conn, brand_id, scheduled_for=NOW + timedelta(hours=6))


def test_one_active_scan_per_brand_across_concurrent_transactions(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as setup:
        owner = add_user(setup, f"{uuid.uuid4().hex}@example.com")
        brand_id = add_brand(setup, owner)
    try:
        with migrated_engine.connect() as first, migrated_engine.connect() as second:
            first_tx = first.begin()
            add_scan(first, brand_id)
            second_tx = second.begin()
            second.execute(text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(OperationalError) as blocked:
                add_scan(second, brand_id, scheduled_for=NOW + timedelta(hours=6))
            assert violation(blocked).sqlstate == LOCK_NOT_AVAILABLE  # waits on the first scan
            second_tx.rollback()
            first_tx.commit()
            retry_tx = second.begin()
            with pytest.raises(IntegrityError) as exc:
                add_scan(second, brand_id, scheduled_for=NOW + timedelta(hours=6))
            assert violation(exc).constraint_name == "uq_scans_brand_id_active"
            retry_tx.rollback()
    finally:
        with migrated_engine.begin() as cleanup:
            cleanup.execute(delete(SCANS).where(SCANS.c.brand_id == brand_id))
            cleanup.execute(delete(table("brands")).where(table("brands").c.id == brand_id))
            cleanup.execute(delete(table("users")).where(table("users").c.id == owner))


def test_unknown_status_is_rejected(conn: Connection) -> None:
    with pytest.raises(DataError) as exc:
        add_scan(conn, brand(conn), status="paused")
    assert violation(exc).sqlstate == "22P02"


def test_brand_with_scans_cannot_be_deleted(conn: Connection) -> None:
    brand_id = brand(conn)
    add_scan(conn, brand_id)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table("brands")).where(table("brands").c.id == brand_id))
    assert violation(exc).constraint_name == "fk_scans_brand_id_brands"


def test_requester_of_a_scan_cannot_be_deleted(conn: Connection) -> None:
    requester = add_user(conn, "requester@example.com")  # owns no brand, so only the scan refers
    add_scan(conn, brand(conn), trigger="manual", scheduled_for=None, requested_by=requester)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table("users")).where(table("users").c.id == requester))
    assert violation(exc).constraint_name == "fk_scans_requested_by_users"


@pytest.mark.parametrize(
    ("pg_type", "enum"),
    [
        ("scan_trigger", ScanTrigger),
        ("scan_status", ScanStatus),
        ("transition_actor", TransitionActor),
        ("surface", Surface),
        ("surface_outcome", SurfaceOutcome),
    ],
)
def test_scan_enums_match_domain(conn: Connection, pg_type: str, enum: type[StrEnum]) -> None:
    labels = conn.execute(text(f"SELECT unnest(enum_range(NULL::{pg_type}))::text")).scalars()
    assert list(labels) == [member.value for member in enum]
