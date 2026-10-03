"""The observation store on real Postgres: Trends lines and app ratings, each written once."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection, insert, select
from sqlalchemy.exc import IntegrityError

from serpsense.adapters.db.observation_store import SqlObservationStore
from serpsense.domain.observation import AppRating, TrendsPoint
from serpsense.ports.observation_store import Comparison, ObservationStore
from tests.integration.db_helpers import NOW, add_app, add_brand, add_scan, add_user, table

pytestmark = pytest.mark.integration

TRENDS, RATINGS = table("trends_observations"), table("app_rating_observations")
DAY = timedelta(days=1)


@pytest.fixture
def store(conn: Connection) -> ObservationStore:
    return SqlObservationStore(conn)


def ola_and_rivals(conn: Connection) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Ola, linked to Uber and Rapido; the user also has an unlinked brand last."""
    owner = add_user(conn)
    brands = [add_brand(conn, owner, slug=slug) for slug in ("ola", "uber", "rapido", "other")]
    for rival in brands[1:3]:
        competitor = {"brand_id": brands[0], "competitor_brand_id": rival}
        conn.execute(insert(table("brand_competitors")).values(**competitor))
    return add_scan(conn, brands[0]), brands


def point(index: int, day: int, interest: int, partial: bool = False) -> TrendsPoint:
    return TrendsPoint("ola" if index == 0 else "rival", index, NOW + day * DAY, interest, partial)


def test_each_line_belongs_to_the_brand_sent_in_its_place(
    conn: Connection, store: ObservationStore
) -> None:
    scan_id, brands = ola_and_rivals(conn)
    # Sent as q=Rapido,Ola: the place, not the name, says whose line it is.
    points = (point(0, 0, 40), point(1, 0, 70), point(0, 1, 45, True), point(1, 1, 0, True))
    comparison = Comparison(scan_id, (brands[2], brands[0]), points)
    assert store.record_trends(comparison) == 4
    assert store.record_trends(comparison) == 0  # a retried scan writes nothing twice
    unlinked = (
        table("brand_competitors")
        .delete()
        .where(table("brand_competitors").c.competitor_brand_id == brands[2])
    )
    conn.execute(unlinked)
    retried = Comparison(scan_id, (brands[2], brands[0]), (point(0, 2, 50), point(1, 2, 60)))
    assert store.record_trends(retried) == 0  # nor mixes in a second normalisation
    columns = (TRENDS.c.subject_brand_id, TRENDS.c.observed_at, TRENDS.c.interest)
    rows = conn.execute(select(*columns, TRENDS.c.is_partial).order_by(*columns)).all()
    assert sorted(tuple(row) for row in rows) == sorted(
        [
            (brands[2], NOW, 40, False),
            (brands[0], NOW, 70, False),
            (brands[2], NOW + DAY, 45, True),
            (brands[0], NOW + DAY, 0, True),
        ]
    )


def test_an_empty_comparison_writes_nothing(conn: Connection, store: ObservationStore) -> None:
    scan_id, brands = ola_and_rivals(conn)
    assert store.record_trends(Comparison(scan_id, (brands[0],), ())) == 0


def test_a_brand_outside_the_comparison_is_refused(
    conn: Connection, store: ObservationStore
) -> None:
    scan_id, brands = ola_and_rivals(conn)
    with pytest.raises(IntegrityError, match="subject_in_comparison"):
        store.record_trends(Comparison(scan_id, (brands[0], brands[3]), (point(1, 0, 5),)))


def test_an_app_rating_is_recorded_once_per_scan(conn: Connection, store: ObservationStore) -> None:
    scan_id, brands = ola_and_rivals(conn)
    app_id = add_app(conn, brands[0])
    assert store.record_app_rating(scan_id, app_id, AppRating(460, 3_540_000)) is True
    assert store.record_app_rating(scan_id, app_id, AppRating(450, 3_540_100)) is False
    row = conn.execute(select(RATINGS.c.rating_hundredths, RATINGS.c.review_count)).one()
    assert tuple(row) == (460, 3_540_000)
