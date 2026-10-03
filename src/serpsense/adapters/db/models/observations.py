"""Append-only observations: what each scan saw (docs/architecture/data-model.md §5).

A mention or app observation always links a scan and a mention/app of the same brand, and a
Trends point's subject is the scan's brand or one of its competitors (database triggers).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, SmallInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base


def _fk(target: str) -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete="RESTRICT"), primary_key=True
    )


class MentionObservation(Base):
    """A mention seen in a scan: first/last seen and rank history derive from these rows."""

    __tablename__ = "mention_observations"
    __table_args__ = (
        # "New since the last scan" reads observations by scan.
        Index("ix_mention_observations_scan_id", "scan_id"),
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint("star_rating BETWEEN 1 AND 5", name="star_rating_range"),
    )

    mention_id: Mapped[uuid.UUID] = _fk("mentions.id")
    scan_id: Mapped[uuid.UUID] = _fk("scans.id")
    position: Mapped[int | None] = mapped_column(SmallInteger)
    star_rating: Mapped[int | None] = mapped_column(SmallInteger)


class AppRatingObservation(Base):
    """An app's store rating and review count as seen in a scan."""

    __tablename__ = "app_rating_observations"
    __table_args__ = (
        Index("ix_app_rating_observations_scan_id", "scan_id"),  # per-scan Play scoring
        CheckConstraint("rating_hundredths BETWEEN 100 AND 500", name="rating_range"),
        CheckConstraint("review_count >= 0", name="review_count_non_negative"),
    )

    brand_app_id: Mapped[uuid.UUID] = _fk("brand_apps.id")
    scan_id: Mapped[uuid.UUID] = _fk("scans.id")
    # 410 means 4.10 stars (integers, no floats).
    rating_hundredths: Mapped[int] = mapped_column(SmallInteger)
    review_count: Mapped[int] = mapped_column(Integer)


class TrendsObservation(Base):
    """One point of a Google Trends joint-query line (scan's brand + up to 4 competitors).

    Rows belong to the scan; `subject_brand_id` says which line it is and must be the scan's
    brand or one of its competitors at insert time (database trigger).
    """

    __tablename__ = "trends_observations"
    __table_args__ = (CheckConstraint("interest BETWEEN 0 AND 100", name="interest_range"),)

    scan_id: Mapped[uuid.UUID] = _fk("scans.id")
    subject_brand_id: Mapped[uuid.UUID] = _fk("brands.id")
    # The point's start (SerpApi's per-point timestamp): hourly or finer for short date ranges.
    observed_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, primary_key=True)
    interest: Mapped[int] = mapped_column(SmallInteger)
    # SerpApi flags the last, still-incomplete point; a drop there is not yet a decline.
    is_partial: Mapped[bool] = mapped_column(Boolean)
