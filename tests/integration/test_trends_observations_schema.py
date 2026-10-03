"""Google Trends observation rules enforced by Postgres itself (data-model.md §5)."""

import uuid
from datetime import UTC, datetime, timedelta
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

TRENDS = table("trends_observations")
COMPETITORS = table("brand_competitors")
START = datetime(2026, 10, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def observe(conn: Connection, scan_id: uuid.UUID, subject: uuid.UUID, **kw: Any) -> None:
    values = {"observed_at": START, "interest": 62, "is_partial": False, **kw}
    conn.execute(insert(TRENDS).values(scan_id=scan_id, subject_brand_id=subject, **values))


def comparison(conn: Connection, rivals: int = 1) -> tuple[uuid.UUID, list[uuid.UUID], uuid.UUID]:
    """A brand with linked competitors, plus a scan of the brand."""
    owner = add_user(conn)
    brand_id = add_brand(conn, owner, slug="voltbox")
    rival_ids = [add_brand(conn, owner, slug=f"rival-{n}") for n in range(rivals)]
    for rival in rival_ids:
        conn.execute(insert(COMPETITORS).values(brand_id=brand_id, competitor_brand_id=rival))
    return brand_id, rival_ids, add_scan(conn, brand_id)


def test_brand_and_competitor_lines_round_trip(conn: Connection) -> None:
    brand_id, [rival], scan_id = comparison(conn)
    observe(conn, scan_id, brand_id, interest=0)  # SerpApi reports "<1" as 0
    observe(conn, scan_id, rival, interest=100, is_partial=True)
    columns = (
        TRENDS.c.subject_brand_id,
        TRENDS.c.observed_at,
        TRENDS.c.interest,
        TRENDS.c.is_partial,
    )
    rows = conn.execute(select(*columns).where(TRENDS.c.scan_id == scan_id)).all()
    expected = [(brand_id, START, 0, False), (rival, START, 100, True)]
    assert sorted(tuple(row) for row in rows) == sorted(expected)


def test_unrelated_brand_is_rejected(conn: Connection) -> None:
    _, _, scan_id = comparison(conn)
    stranger = add_brand(conn, add_user(conn, "other@example.com"), slug="boat")
    with pytest.raises(IntegrityError) as exc:
        observe(conn, scan_id, stranger)
    assert violation(exc).constraint_name == "ck_trends_observations_subject_in_comparison"


def test_competitor_direction_matters(conn: Connection) -> None:
    """The rival's own scan may not chart the brand that lists it as a competitor."""
    brand_id, [rival], _ = comparison(conn)
    rival_scan = add_scan(conn, rival)
    with pytest.raises(IntegrityError) as exc:
        observe(conn, rival_scan, brand_id)
    assert violation(exc).constraint_name == "ck_trends_observations_subject_in_comparison"


@pytest.mark.parametrize(("missing", "target"), [("scan", "scans"), ("subject_brand", "brands")])
def test_missing_scan_or_subject_reports_the_foreign_key(
    conn: Connection, missing: str, target: str
) -> None:
    brand_id, _, scan_id = comparison(conn)
    ids = {"scan": scan_id, "subject_brand": brand_id, missing: uuid.uuid4()}
    with pytest.raises(IntegrityError) as exc:
        observe(conn, ids["scan"], ids["subject_brand"])
    assert violation(exc).constraint_name == f"fk_trends_observations_{missing}_id_{target}"


def test_a_comparison_has_at_most_five_lines(conn: Connection) -> None:
    brand_id, rivals, scan_id = comparison(conn, rivals=5)
    for subject in [brand_id, *rivals[:4]]:
        observe(conn, scan_id, subject)
    observe(conn, scan_id, brand_id, observed_at=START + HOUR)  # more points on a line
    with pytest.raises(IntegrityError) as exc:
        observe(conn, scan_id, rivals[4])
    assert violation(exc).constraint_name == "ck_trends_observations_comparison_size"


def test_unlinking_a_competitor_keeps_past_points(conn: Connection) -> None:
    brand_id, [rival], scan_id = comparison(conn)
    observe(conn, scan_id, rival)
    conn.execute(delete(COMPETITORS).where(COMPETITORS.c.brand_id == brand_id))
    stored = conn.execute(select(TRENDS.c.subject_brand_id).where(TRENDS.c.scan_id == scan_id))
    assert stored.scalars().all() == [rival]


@pytest.mark.parametrize("interest", [-1, 101])
def test_interest_range(conn: Connection, interest: int) -> None:
    brand_id, _, scan_id = comparison(conn)
    with pytest.raises(IntegrityError) as exc:
        observe(conn, scan_id, brand_id, interest=interest)
    assert violation(exc).constraint_name == "ck_trends_observations_interest_range"


def test_one_point_per_subject_per_timestamp(conn: Connection) -> None:
    brand_id, _, scan_id = comparison(conn)
    observe(conn, scan_id, brand_id)
    observe(conn, scan_id, brand_id, observed_at=START + HOUR)  # hourly points fit
    with pytest.raises(IntegrityError) as exc:
        observe(conn, scan_id, brand_id, interest=1)
    assert violation(exc).constraint_name == "pk_trends_observations"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_trends_are_append_only(conn: Connection, mutation: str) -> None:
    brand_id, _, scan_id = comparison(conn)
    observe(conn, scan_id, brand_id)
    statements: dict[str, Executable] = {
        "update": update(TRENDS).where(TRENDS.c.scan_id == scan_id).values(interest=1),
        "delete": delete(TRENDS).where(TRENDS.c.scan_id == scan_id),
        "truncate": text("TRUNCATE trends_observations"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION
