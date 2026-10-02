"""Versioned settings (docs/architecture/data-model.md §2).

Every table here is append-only: a change is a new row, and the current version is the latest
`created_at` per key (unique per key and instant).
"""

import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base

DEFAULT_TIMEZONE = "Asia/Kolkata"


def _fk(target: str) -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete="RESTRICT"))


def _document_version_args(key: str) -> tuple[Any, ...]:
    return (
        UniqueConstraint(key, "created_at"),
        CheckConstraint("jsonb_typeof(document) = 'object'", name="document_is_object"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
    )


class UserLlmProfileVersion(Base):
    """Per-task model/effort/display/output settings and preset (validated by Pydantic)."""

    __tablename__ = "user_llm_profile_versions"
    __table_args__ = _document_version_args("user_id")

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class UserSearchDefaultVersion(Base):
    """User-level SerpApi defaults: country, languages, device, cache, concurrency, caps."""

    __tablename__ = "user_search_default_versions"
    __table_args__ = _document_version_args("user_id")

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class BrandSearchSettingsVersion(Base):
    """Per-brand, per-engine SerpApi knobs (templates, pages, filters, TTLs)."""

    __tablename__ = "brand_search_settings_versions"
    __table_args__ = _document_version_args("brand_id")

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = _fk("brands.id")
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class BrandScheduleVersion(Base):
    """Relational (the dispatcher filters on it): scan interval and quiet hours."""

    __tablename__ = "brand_schedule_versions"
    __table_args__ = (
        UniqueConstraint("brand_id", "created_at"),
        # NULL interval passes (manual scans only).
        CheckConstraint("interval_minutes IN (60, 180, 360, 720, 1440)", name="interval_allowed"),
        CheckConstraint(
            "(quiet_start IS NULL AND quiet_end IS NULL) OR "
            "(quiet_start IS NOT NULL AND quiet_end IS NOT NULL AND quiet_start <> quiet_end)",
            name="quiet_hours_valid",
        ),
        CheckConstraint("timezone ~ '^\\S{1,64}$'", name="timezone_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = _fk("brands.id")
    # NULL = manual scans only.
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(Text, server_default=text(f"'{DEFAULT_TIMEZONE}'"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
