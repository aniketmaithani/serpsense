"""The dispatcher's read of scheduled brands on real Postgres (data-model §2, §3)."""

import uuid
from datetime import time, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, insert, update

from serpsense.adapters.db.scheduled_brands import SqlScheduledBrands
from serpsense.domain.estimator import BrandFacts
from serpsense.ports.scheduled_brands import ScheduledBrands
from tests.integration.db_helpers import (
    NOW,
    add,
    add_app,
    add_brand,
    add_location,
    add_scan,
    add_user,
    table,
)

pytestmark = pytest.mark.integration

HOUR = timedelta(hours=1)
LATER = NOW + timedelta(days=1)


def schedule(conn: Connection, brand_id: uuid.UUID, interval: int | None, **kw: Any) -> None:
    values = {"interval_minutes": interval, "created_at": NOW, **kw}
    add(conn, table("brand_schedule_versions"), brand_id=brand_id, **values)


def document(conn: Connection, name: str, key: str, owner: uuid.UUID, **kw: Any) -> None:
    values = {key: owner, "schema_version": 1, "created_at": NOW, **kw}
    add(conn, table(name), **values)


@pytest.fixture
def reader(conn: Connection) -> ScheduledBrands:
    return SqlScheduledBrands(conn)


def test_a_scheduled_brand_comes_with_everything_its_scan_needs(
    conn: Connection, reader: ScheduledBrands
) -> None:
    owner = add_user(conn)
    brand_id = add_brand(conn, owner, slug="ola")
    schedule(conn, brand_id, 360)
    quiet = {"quiet_start": time(0), "quiet_end": time(6)}
    schedule(conn, brand_id, 720, timezone="UTC", created_at=NOW + HOUR, **quiet)
    defaults = "user_search_default_versions"
    document(conn, defaults, "user_id", owner, document={"country": "us"})
    later = {"country": "in", "max_searches": 10}
    document(conn, defaults, "user_id", owner, document=later, created_at=NOW + HOUR)
    document(conn, "brand_search_settings_versions", "brand_id", brand_id, document={"maps": {}})
    for code in ("hi", "en"):
        conn.execute(insert(table("brand_languages")).values(brand_id=brand_id, language_code=code))
    add_app(conn, brand_id)
    add_app(conn, brand_id, app_id="com.olacabs.driver")
    add_location(conn, brand_id)
    add_location(conn, brand_id, query="Ola Koramangala", resolved_data_id="0x1", resolved_at=NOW)
    add_scan(conn, brand_id, status="succeeded")
    add_scan(conn, brand_id, scheduled_for=NOW + HOUR, created_at=NOW + HOUR)

    (brand,) = reader.scheduled_brands(LATER)
    assert (brand.brand_id, brand.interval_minutes, brand.timezone) == (brand_id, 720, "UTC")
    assert (brand.quiet_start, brand.quiet_end) == (time(0), time(6))
    assert brand.user_defaults == {"country": "in", "max_searches": 10}
    assert brand.brand_settings == {"maps": {}}
    assert brand.languages == ("en", "hi")
    assert brand.facts == BrandFacts(apps=2, locations=2, unresolved_locations=1)
    assert brand.last_scan_at == NOW + HOUR


def test_only_live_brands_on_a_schedule_are_read(conn: Connection, reader: ScheduledBrands) -> None:
    owner = add_user(conn)
    bare = add_brand(conn, owner, slug="bare")  # scheduled, nothing else
    schedule(conn, bare, 60)
    archived = add_brand(conn, owner, slug="archived", archived_at=NOW)
    schedule(conn, archived, 60)
    manual = add_brand(conn, owner, slug="manual")  # its latest version is manual only
    schedule(conn, manual, 60)
    schedule(conn, manual, None, created_at=NOW + HOUR)
    add_brand(conn, owner, slug="unscheduled")
    gone = add_user(conn, "gone@example.com")
    schedule(conn, add_brand(conn, gone, slug="orphan"), 60)
    conn.execute(update(table("users")).where(table("users").c.id == gone).values(deleted_at=NOW))

    (brand,) = reader.scheduled_brands(LATER)
    assert brand.brand_id == bare
    assert (brand.user_defaults, brand.brand_settings, brand.languages) == ({}, {}, ())
    assert (brand.facts, brand.last_scan_at) == (BrandFacts(apps=0, locations=0), None)


def test_later_versions_and_scans_that_cover_no_slot_are_ignored(
    conn: Connection, reader: ScheduledBrands
) -> None:
    brand_id = add_brand(conn, add_user(conn), slug="ola")
    schedule(conn, brand_id, 360)
    schedule(conn, brand_id, 60, created_at=NOW + 2 * HOUR)  # not in force yet
    add_scan(conn, brand_id, status="succeeded")  # the one that counts
    for minutes, status in ((30, "skipped"), (60, "failed")):
        at = NOW + timedelta(minutes=minutes)
        add_scan(conn, brand_id, status=status, scheduled_for=at, created_at=at)
    replay = {"trigger": "replay", "scheduled_for": None, "status": "succeeded"}
    add_scan(conn, brand_id, created_at=NOW + HOUR, **replay)
    later = NOW + 3 * HOUR
    add_scan(conn, brand_id, status="succeeded", scheduled_for=later, created_at=later)

    (brand,) = reader.scheduled_brands(NOW + 2 * HOUR - timedelta(seconds=1))
    assert (brand.interval_minutes, brand.last_scan_at) == (360, NOW)


def test_scan_now_reads_one_live_brand_of_its_owner(
    conn: Connection, reader: ScheduledBrands
) -> None:
    owner, stranger = add_user(conn), add_user(conn, "else@example.com")
    brand_id = add_brand(conn, owner, slug="ola")  # no schedule: "Scan now" still works
    document(conn, "user_search_default_versions", "user_id", owner, document={"country": "in"})
    later = {"document": {"country": "us"}, "created_at": LATER + HOUR}  # not yet
    document(conn, "user_search_default_versions", "user_id", owner, **later)
    conn.execute(insert(table("brand_languages")).values(brand_id=brand_id, language_code="hi"))
    add_app(conn, brand_id)

    inputs = reader.scan_inputs(owner, brand_id, LATER)
    assert inputs is not None
    assert (inputs.user_defaults, inputs.brand_settings) == ({"country": "in"}, {})
    assert inputs.languages == ("hi",) and inputs.facts == BrandFacts(apps=1, locations=0)
    assert inputs.last_scan_at is None
    add_scan(conn, brand_id, status="succeeded", scheduled_for=None, trigger="manual",
             requested_by=owner)  # fmt: skip
    scanned = reader.scan_inputs(owner, brand_id, LATER)
    assert scanned is not None and scanned.last_scan_at == NOW
    assert reader.scan_inputs(stranger, brand_id, LATER) is None  # someone else's: missing
    assert reader.scan_inputs(owner, uuid.uuid4(), LATER) is None

    archived = add_brand(conn, owner, slug="old", archived_at=NOW)
    assert reader.scan_inputs(owner, archived, LATER) is None
    conn.execute(update(table("users")).where(table("users").c.id == owner).values(deleted_at=NOW))
    assert reader.scan_inputs(owner, brand_id, LATER) is None
