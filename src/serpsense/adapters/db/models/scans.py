"""Scan tables (docs/architecture/data-model.md §4)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import (
    ScanStatus,
    ScanTrigger,
    Surface,
    SurfaceOutcome,
    TransitionActor,
)

SCAN_STATUS = pg_enum(ScanStatus, "scan_status")
# Machine-readable codes (e.g. claimed, timed_out, budget_exhausted), never free text or PII.
CODE_FORMAT = "~ '^[a-z][a-z0-9_.]{0,63}$'"


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
    status: Mapped[ScanStatus] = mapped_column(SCAN_STATUS)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estimated_searches: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class ScanStatusTransition(Base):
    """Append-only history of scan status changes; started/finished times derive from it.

    Postgres rejects any transition outside the scan state machine (defence in depth for
    domain.scan_state, which is the single place that decides transitions).
    """

    __tablename__ = "scan_status_transitions"
    __table_args__ = (
        Index("ix_scan_status_transitions_scan_id_at", "scan_id", "at"),
        # No scan reaches the same status twice (catches a duplicate creation or finish).
        UniqueConstraint("scan_id", "to_status"),
        CheckConstraint(
            "CASE"
            " WHEN from_status IS NULL THEN to_status = 'queued'"
            " WHEN from_status = 'queued' THEN to_status IN ('running', 'skipped')"
            " WHEN from_status = 'running'"
            " THEN to_status IN ('succeeded', 'partial', 'failed', 'skipped')"
            " ELSE false END",
            name="allowed_transition",
        ),
        CheckConstraint(
            "(actor = 'user') = (actor_user_id IS NOT NULL)", name="actor_user_matches"
        ),
        CheckConstraint(f"reason {CODE_FORMAT}", name="reason_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="RESTRICT")
    )
    from_status: Mapped[ScanStatus | None] = mapped_column(SCAN_STATUS)
    to_status: Mapped[ScanStatus] = mapped_column(SCAN_STATUS)
    actor: Mapped[TransitionActor] = mapped_column(pg_enum(TransitionActor, "transition_actor"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class ScanSurfaceResult(Base):
    """Append-only: how each surface went in a scan (drives the "partial scan" UI)."""

    __tablename__ = "scan_surface_results"
    __table_args__ = (
        PrimaryKeyConstraint("scan_id", "surface"),
        CheckConstraint(
            "(outcome = 'failed') = (error_code IS NOT NULL)", name="error_code_iff_failed"
        ),
        CheckConstraint(f"error_code {CODE_FORMAT}", name="error_code_format"),
    )

    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="RESTRICT")
    )
    surface: Mapped[Surface] = mapped_column(pg_enum(Surface, "surface"))
    outcome: Mapped[SurfaceOutcome] = mapped_column(pg_enum(SurfaceOutcome, "surface_outcome"))
    error_code: Mapped[str | None] = mapped_column(Text)
