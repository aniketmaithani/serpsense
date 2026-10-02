"""Brand tables (docs/architecture/data-model.md §3)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base
from serpsense.domain.enums import AppStore

APP_STORE = Enum(
    AppStore, name="app_store", values_callable=lambda members: [m.value for m in members]
)


def _brand_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT"))


class Brand(Base):
    """A monitored brand; competitors are brands too. Archived, never deleted (ADR-0013)."""

    __tablename__ = "brands"
    __table_args__ = (
        # Lookups by owner use this constraint's index (owner_id is its leading column).
        UniqueConstraint("owner_id", "slug"),
        CheckConstraint("name ~ '\\S' AND char_length(name) <= 120", name="name_length"),
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


class BrandAlias(Base):
    """Other names the brand goes by; unique per brand ignoring case (citext)."""

    __tablename__ = "brand_aliases"
    __table_args__ = (
        UniqueConstraint("brand_id", "alias"),
        CheckConstraint("alias ~ '\\S' AND char_length(alias) <= 120", name="alias_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = _brand_fk()
    alias: Mapped[str] = mapped_column(CITEXT)


class BrandLanguage(Base):
    """Languages to search in, as lowercase BCP-47 tags (e.g. `en`, `hi`, `zh-cn`)."""

    __tablename__ = "brand_languages"
    __table_args__ = (
        CheckConstraint(
            "language_code ~ '^[a-z]{2,3}(-[a-z0-9]{2,8})*$'", name="language_code_format"
        ),
    )

    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT"), primary_key=True
    )
    language_code: Mapped[str] = mapped_column(Text, primary_key=True)


class BrandWatchTerm(Base):
    """Words that flag a negative mention (e.g. refund, scam); unique per brand ignoring case."""

    __tablename__ = "brand_watch_terms"
    __table_args__ = (
        UniqueConstraint("brand_id", "term"),
        CheckConstraint("term ~ '\\S' AND char_length(term) <= 80", name="term_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = _brand_fk()
    term: Mapped[str] = mapped_column(CITEXT)


class BrandApp(Base):
    """A companion app whose store reviews are collected."""

    __tablename__ = "brand_apps"
    __table_args__ = (
        UniqueConstraint("brand_id", "store", "app_id"),
        CheckConstraint("app_id ~ '^\\S{1,255}$'", name="app_id_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = _brand_fk()
    store: Mapped[AppStore] = mapped_column(APP_STORE)
    app_id: Mapped[str] = mapped_column(Text)


class BrandLocation(Base):
    """A Maps search for a physical location; resolved once to a place data id."""

    __tablename__ = "brand_locations"
    __table_args__ = (
        UniqueConstraint("brand_id", "query"),
        CheckConstraint("query ~ '\\S' AND char_length(query) <= 200", name="query_length"),
        CheckConstraint(
            "(resolved_data_id IS NULL) = (resolved_at IS NULL)", name="resolution_together"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = _brand_fk()
    query: Mapped[str] = mapped_column(CITEXT)
    resolved_data_id: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
