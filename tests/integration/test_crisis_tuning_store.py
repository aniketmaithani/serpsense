"""A brand's crisis tuning on real Postgres: stored as versions, read by v_scan_scores for its
levels and by the alert store for its cooldown and spread rule."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection, delete, text, update
from sqlalchemy.exc import IntegrityError

from serpsense.adapters.db.alert_store import SqlAlertStore
from serpsense.adapters.db.brand_store import SqlBrandStore
from serpsense.domain.enums import CrisisComponent, Surface
from serpsense.domain.scoring.tuning import DEFAULT_TUNING, CrisisTuning
from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add_owned_brand,
    table,
    violation,
)
from tests.integration.test_scan_scores_view import scan_at, scored, view

pytestmark = pytest.mark.integration

TUNINGS = table("brand_crisis_tuning_versions")
SPIKE = {CrisisComponent.VELOCITY: 90, CrisisComponent.PRESS: 75}  # crisis 35
HOUR = timedelta(hours=1)
TOUCHY = CrisisTuning(warm_up_scans=0, medium_at=30, high_at=60, cooldown_hours=2)


def test_a_tuning_is_versioned_and_defaults_until_set(conn: Connection) -> None:
    store, brand_id = SqlBrandStore(conn), add_owned_brand(conn)
    assert store.crisis_tuning(brand_id) == DEFAULT_TUNING
    assert not store.set_crisis_tuning(brand_id, DEFAULT_TUNING, at=NOW)  # nothing to change
    assert store.set_crisis_tuning(brand_id, TOUCHY, at=NOW)
    assert not store.set_crisis_tuning(brand_id, TOUCHY, at=NOW)  # the same again
    assert store.set_crisis_tuning(brand_id, DEFAULT_TUNING, at=NOW + HOUR)  # back to the defaults
    assert store.crisis_tuning(brand_id) == DEFAULT_TUNING


@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_tunings_are_append_only(conn: Connection, mutation: str) -> None:
    brand_id = add_owned_brand(conn)
    SqlBrandStore(conn).set_crisis_tuning(brand_id, TOUCHY, at=NOW)
    mine = TUNINGS.c.brand_id == brand_id
    statement = (
        update(TUNINGS).where(mine).values(medium_at=10)
        if mutation == "update"
        else delete(TUNINGS).where(mine)
    )
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statement)
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


def test_the_database_refuses_a_high_at_or_below_medium(conn: Connection) -> None:
    row = {"id": uuid.uuid4(), "brand_id": add_owned_brand(conn), "created_at": NOW}
    row |= {"warm_up_scans": 3, "medium_at": 50, "high_at": 50, "cooldown_hours": 12}
    row |= {"spread_mentions": 5, "spread_surfaces": 2}
    with pytest.raises(IntegrityError) as exc:
        conn.execute(TUNINGS.insert().values(row))
    assert violation(exc).constraint_name == "ck_brand_crisis_tuning_versions_high_above_medium"


def test_the_view_reads_every_scan_with_the_brands_latest_tuning(conn: Connection) -> None:
    brand_id, other = add_owned_brand(conn), add_owned_brand(conn, "other")
    first = scan_at(conn, brand_id, 0, "succeeded")
    scored(conn, first, {Surface.NEWS: 50}, SPIKE)
    theirs = scan_at(conn, other, 0, "succeeded")
    scored(conn, theirs, {Surface.NEWS: 50}, SPIKE)
    assert view(conn, first)[1:] == (35, None)  # warming up under the defaults
    SqlBrandStore(conn).set_crisis_tuning(brand_id, TOUCHY, at=NOW)
    assert view(conn, first)[1:] == (35, "medium")  # no warm-up, medium from 30
    assert view(conn, theirs)[2] is None  # another brand keeps the defaults
    SqlBrandStore(conn).set_crisis_tuning(
        brand_id, CrisisTuning(warm_up_scans=0, medium_at=20, high_at=35), at=NOW + HOUR
    )
    assert view(conn, first)[2] == "high"


def test_the_alert_rules_get_the_brands_tuning(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    scan_id = scan_at(conn, brand_id, 0, "succeeded")
    scored(conn, scan_id, {Surface.NEWS: 50}, SPIKE)
    store = SqlAlertStore(conn)
    before = store.context(scan_id)
    assert before is not None and before.facts.tuning == DEFAULT_TUNING
    SqlBrandStore(conn).set_crisis_tuning(brand_id, TOUCHY, at=NOW)
    context = store.context(scan_id)
    assert context is not None and context.facts.tuning == TOUCHY
    assert context.facts.level is not None and context.facts.level.value == "medium"
    levels = text("SELECT count(*) FROM v_scan_scores WHERE brand_id = :b")
    assert conn.execute(levels, {"b": brand_id}).scalar_one() == 1
