"""Brand tables (docs/architecture/data-model.md §3)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import Base

TIMESTAMPTZ = DateTime(timezone=True)


class Brand(Base):
    """A monitored brand; competitors are brands too. Archived, never deleted (ADR-0013)."""

    __tablename__ = "brands"
    __table_args__ = (
        # Lookups by owner use this constraint's index (owner_id is its leading column).
        UniqueConstraint("owner_id", "slug"),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 120", name="name_length"),
        CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) <= 64", name="slug_format"
        ),
        CheckConstraint("char_length(tone_notes) <= 2000", name="tone_notes_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # Immutable (database trigger): the same-owner rule for competitors depends on it.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text)
    tone_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class BrandCompetitor(Base):
    """Brand and competitor must share an owner (database trigger)."""

    __tablename__ = "brand_competitors"
    __table_args__ = (
        CheckConstraint("brand_id <> competitor_brand_id", name="not_self"),
        Index("ix_brand_competitors_competitor_brand_id", "competitor_brand_id"),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT"), primary_key=True
    )
    competitor_brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT"), primary_key=True
    )
