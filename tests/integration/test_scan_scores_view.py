"""A scan's scores on real Postgres: v_scan_scores derives what domain.scoring computes."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from serpsense.domain.enums import CrisisComponent, Surface
from serpsense.domain.scoring import crisis as crisis_rules
from serpsense.domain.scoring import surfaces as surface_rules
from tests.integration.db_helpers import NOW, add_owned_brand, add_scan, table

pytestmark = pytest.mark.integration

RUNS, SURFACES, COMPONENTS = (
    table("score_runs"),
    table("surface_scores"),
    table("crisis_components"),
)
SEEN = {Surface.SEARCH_PAGE: 81, Surface.NEWS: 40, Surface.PLAY: 63}
SPIKE = {CrisisComponent.VELOCITY: 90, CrisisComponent.PRESS: 75}


def scored(
    conn: Connection,
    scan_id: uuid.UUID,
    surfaces: dict[Surface, int],
    components: dict[CrisisComponent, int],
) -> None:
    conn.execute(insert(RUNS).values(scan_id=scan_id, version="s1", computed_at=NOW))
    for surface, score in surfaces.items():
        conn.execute(insert(SURFACES).values(scan_id=scan_id, surface=surface, score=score))
    for component, value in components.items():
        conn.execute(insert(COMPONENTS).values(scan_id=scan_id, component=component, value=value))


def view(conn: Connection, scan_id: uuid.UUID) -> tuple[object, ...]:
    query = text("SELECT health, crisis, crisis_level FROM v_scan_scores WHERE scan_id = :id")
    return tuple(conn.execute(query, {"id": scan_id}).one())


def scan_at(conn: Connection, brand_id: uuid.UUID, hours: int, status: str) -> uuid.UUID:
    at = NOW + timedelta(hours=hours)
    return add_scan(conn, brand_id, status=status, scheduled_for=at, created_at=at)


def test_the_view_derives_what_the_domain_computes(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    scans = [scan_at(conn, brand_id, 12 * n, "succeeded") for n in range(4)]
    for scan_id in scans:
        scored(conn, scan_id, SEEN, SPIKE)
    health, crisis = surface_rules.health(SEEN), crisis_rules.crisis(SPIKE)
    for earlier, scan_id in enumerate(scans):
        level = crisis_rules.level(crisis).value if crisis_rules.has_level(earlier) else None
        assert view(conn, scan_id) == (health, crisis, level)
    assert (health, crisis) == (65, 35)  # by hand: 357000 / 5500 = 64.9 and 34.5, half up
    by_brand = text("SELECT count(*) FROM v_scan_scores WHERE brand_id = :b")
    assert conn.execute(by_brand, {"b": brand_id}).scalar_one() == 4


def test_the_warm_up_counts_this_brands_earlier_scored_scans_only(conn: Connection) -> None:
    brand_id, other = add_owned_brand(conn), add_owned_brand(conn, "other")
    scored(conn, scan_at(conn, brand_id, 0, "partial"), SEEN, {})  # scored: counts
    scan_at(conn, brand_id, 12, "failed")  # never scored: doesn't
    scored(conn, scan_at(conn, brand_id, 24, "succeeded"), SEEN, {})
    for hours in (1, 2, 3):  # another brand's: don't
        scored(conn, scan_at(conn, other, hours, "succeeded"), SEEN, {})
    third = scan_at(conn, brand_id, 36, "succeeded")
    scored(conn, third, SEEN, SPIKE)
    assert view(conn, third)[2] is None  # two earlier scored scans: still warming up
    fourth = scan_at(conn, brand_id, 48, "succeeded")
    scored(conn, fourth, SEEN, SPIKE)
    assert view(conn, fourth)[2] == crisis_rules.level(crisis_rules.crisis(SPIKE)).value


def test_a_scan_that_showed_nothing_has_no_health_and_a_calm_crisis(conn: Connection) -> None:
    scan_id = add_scan(conn, add_owned_brand(conn, "calm"), status="succeeded")
    scored(conn, scan_id, {}, {})
    assert view(conn, scan_id) == (None, 0, None)


def test_a_scans_scores_never_change(conn: Connection) -> None:
    scan_id = add_scan(conn, add_owned_brand(conn, "fixed"), status="succeeded")
    scored(conn, scan_id, {Surface.NEWS: 50}, {})
    with pytest.raises(DBAPIError, match="append-only"), conn.begin_nested():
        conn.execute(update(SURFACES).values(score=99))
    assert conn.execute(select(SURFACES.c.score)).scalars().all() == [50]
