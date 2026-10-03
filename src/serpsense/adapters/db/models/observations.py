"""Append-only observations: what each scan saw (docs/architecture/data-model.md §5).

An observation always links a scan and a mention/app of the same brand (database trigger).
"""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, SmallInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import Base


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
