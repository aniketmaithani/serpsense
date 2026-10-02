"""SerpApi call ledger and raw responses (docs/architecture/data-model.md §5)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.adapters.db.ddl import no_api_key_check
from serpsense.domain.enums import SerpCallOutcome, SerpEngine, ServedFrom


class SerpCall(Base):
    """Append-only ledger: one row per SerpApi request attempt (billable ⇔ served live)."""

    __tablename__ = "serp_calls"
    __table_args__ = (
        Index("ix_serp_calls_user_id_created_at", "user_id", "created_at"),
        # The circuit breaker reads the last calls per engine.
        Index("ix_serp_calls_engine_created_at", "engine", "created_at"),
        Index("ix_serp_calls_scan_id", "scan_id"),
        # Only successful calls have a source, so billable (live) excludes failures and skips.
        CheckConstraint(
            "(outcome = 'succeeded') = (served_from IS NOT NULL)", name="served_from_iff_succeeded"
        ),
        CheckConstraint(
            "(outcome = 'failed') = (error_code IS NOT NULL)", name="error_code_iff_failed"
        ),
        CheckConstraint("error_code ~ '^[a-z][a-z0-9_.]{0,63}$'", name="error_code_format"),
        CheckConstraint("http_status BETWEEN 100 AND 599", name="http_status_range"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint("params_hash ~ '^[0-9a-f]{64}$'", name="params_hash_sha256"),
        CheckConstraint("jsonb_typeof(params) = 'object'", name="params_is_object"),
        # A leaked key could never be removed from this append-only ledger (ADR-0007).
        CheckConstraint(no_api_key_check("params"), name="params_no_api_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    # NULL for Preview calls made outside a scan.
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="RESTRICT")
    )
    engine: Mapped[SerpEngine] = mapped_column(pg_enum(SerpEngine, "serp_engine"))
    # sha256 of the canonical params; the API key is never part of params or the hash input.
    params_hash: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    served_from: Mapped[ServedFrom | None] = mapped_column(pg_enum(ServedFrom, "served_from"))
    outcome: Mapped[SerpCallOutcome] = mapped_column(pg_enum(SerpCallOutcome, "serp_call_outcome"))
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    error_code: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class RawResponse(Base):
    """The redacted SerpApi payload of a call, kept for replay and re-parsing.

    Mutable on purpose (not append-only) so a retention job can prune old payloads.
    """

    __tablename__ = "raw_responses"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_is_object"),
        CheckConstraint(no_api_key_check("payload"), name="payload_no_api_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    serp_call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("serp_calls.id", ondelete="RESTRICT"), unique=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
