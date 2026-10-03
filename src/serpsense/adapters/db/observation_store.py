"""Trends lines and app ratings in Postgres (data-model §5)."""

import uuid
from typing import cast

from sqlalchemy import Connection, Table, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from serpsense.adapters.db.models.observations import AppRatingObservation, TrendsObservation
from serpsense.domain.observation import AppRating
from serpsense.ports.observation_store import Comparison

TRENDS = cast(Table, TrendsObservation.__table__)
RATINGS = cast(Table, AppRatingObservation.__table__)


class SqlObservationStore:
    """Works on the caller's connection and never commits: the unit of work does. The database
    checks that each subject is the scan's brand or one of its competitors."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record_trends(self, comparison: Comparison) -> int:
        recorded = select(TRENDS.c.scan_id).where(TRENDS.c.scan_id == comparison.scan_id)
        if not comparison.points or self._conn.execute(recorded.limit(1)).first() is not None:
            return 0  # one collector writes a scan's comparison once
        rows = [
            {
                "scan_id": comparison.scan_id,
                "subject_brand_id": comparison.subjects[point.query_index],
                "observed_at": point.observed_at,
                "interest": point.interest,
                "is_partial": point.is_partial,
            }
            for point in comparison.points
        ]
        self._conn.execute(insert(TRENDS).values(rows))
        return len(rows)

    def record_app_rating(
        self, scan_id: uuid.UUID, brand_app_id: uuid.UUID, rating: AppRating
    ) -> bool:
        statement = (
            pg_insert(RATINGS)
            .values(
                scan_id=scan_id,
                brand_app_id=brand_app_id,
                rating_hundredths=rating.rating_hundredths,
                review_count=rating.review_count,
            )
            .on_conflict_do_nothing()
            .returning(RATINGS.c.scan_id)
        )
        return self._conn.execute(statement).first() is not None
