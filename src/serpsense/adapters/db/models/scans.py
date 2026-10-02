"""Scan tables (docs/architecture/data-model.md §4)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import ScanStatus, ScanTrigger


class Scan(Base):
    """One scan of one brand. `status` is a named exception to "no derived values" (see doc).

    Only `status` can change after insert (trigger `trg_scans_identity_immutable`).
    """

    __tablename__ = "scans"
    __table_args__ = (
        # NULLs are distinct, so only scheduled scans can collide on a slot.
        UniqueConstraint("brand_id", "scheduled_for"),
        # At most one active scan per brand; "Scan now" during an active scan is rejected.
        Index(
            "uq_scans_brand_id_active",
            "brand_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_scans_brand_id_created_at", "brand_id", "created_at"),
        CheckConstraint(
            "(\"trigger\" = 'schedule') = (scheduled_for IS NOT NULL)",
            name="scheduled_for_schedule",
        ),
        CheckConstraint(
            "(\"trigger\" = 'manual') = (requested_by IS NOT NULL)", name="requested_by_manual"
        ),
        CheckConstraint(
            "jsonb_typeof(settings_snapshot) = 'object'", name="settings_snapshot_is_object"
        ),
        CheckConstraint("estimated_searches >= 0", name="estimated_searches_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT")
    )
    trigger: Mapped[ScanTrigger] = mapped_column(pg_enum(ScanTrigger, "scan_trigger"))
    scheduled_for: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    status: Mapped[ScanStatus] = mapped_column(pg_enum(ScanStatus, "scan_status"))
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estimated_searches: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
