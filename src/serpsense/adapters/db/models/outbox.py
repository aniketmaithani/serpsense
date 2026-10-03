"""The email outbox (docs/architecture/data-model.md §8, ADR-0010)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import OutboxKind, OutboxOutcome, OutboxStatus


def _fk(target: str) -> Mapped[uuid.UUID | None]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{target}.id", ondelete="RESTRICT"))


class OutboxMessage(Base):
    """An email to send, written in the same transaction as what caused it. Mutable: `status`
    (a named exception, written only with an attempt row) and the account-deletion scrub of the
    recipient. The encrypted payload is nulled once the message is no longer pending."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        Index(
            "ix_outbox_messages_pending",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
        CheckConstraint(
            "(kind = 'otp_email') = (otp_code_id IS NOT NULL) "
            "AND (kind = 'alert_email') = (alert_id IS NOT NULL)",
            name="kind_refs",
        ),
        CheckConstraint(
            "status = 'pending' OR sensitive_data_encrypted IS NULL", name="sensitive_only_pending"
        ),
        CheckConstraint(
            "dedupe_key ~ '\\S' AND char_length(dedupe_key) <= 200", name="dedupe_key_length"
        ),
        CheckConstraint("recipient_email ~ '^[^@\\s,;<>]+@[^@\\s,;<>]+$'", name="recipient_plain"),
        CheckConstraint(
            "kind <> 'alert_email' OR user_id IS NOT NULL", name="alert_email_has_user"
        ),
        Index("ix_outbox_messages_user_id", "user_id"),  # the account-deletion scrub
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[OutboxKind] = mapped_column(pg_enum(OutboxKind, "outbox_kind"))
    user_id: Mapped[uuid.UUID | None] = _fk("users")
    otp_code_id: Mapped[uuid.UUID | None] = _fk("otp_codes")
    alert_id: Mapped[uuid.UUID | None] = _fk("alerts")
    recipient_email: Mapped[str] = mapped_column(CITEXT)
    template: Mapped[str] = mapped_column(Text)
    template_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # non-sensitive only
    sensitive_data_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)  # MultiFernet
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[OutboxStatus] = mapped_column(pg_enum(OutboxStatus, "outbox_status"))
    next_attempt_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class OutboxAttempt(Base):
    """Append-only: one row per send attempt; the message's status follows from these."""

    __tablename__ = "outbox_attempts"
    __table_args__ = (
        Index(
            "ix_outbox_attempts_outbox_message_id_attempted_at", "outbox_message_id", "attempted_at"
        ),
        CheckConstraint("error_code ~ '^[a-z][a-z0-9_.]{0,63}$'", name="error_code_format"),
        CheckConstraint(
            "(outcome IN ('retryable_error', 'permanent_error')) = (error_code IS NOT NULL)",
            name="error_code_iff_error",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    outbox_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outbox_messages.id", ondelete="RESTRICT")
    )
    attempted_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    outcome: Mapped[OutboxOutcome] = mapped_column(pg_enum(OutboxOutcome, "outbox_attempt_outcome"))
    error_code: Mapped[str | None] = mapped_column(Text)
