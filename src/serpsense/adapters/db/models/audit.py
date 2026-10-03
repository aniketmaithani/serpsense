"""The audit log (docs/architecture/data-model.md §9, ADR-0009 and ADR-0013)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base


class AuditEvent(Base):
    """Append-only: who did what to what, and when. Never an email, IP, code or token: network
    details live in `audit_event_network`, which account deletion scrubs."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_actor_user_id_created_at", "actor_user_id", "created_at"),
        Index("ix_audit_events_target_id", "target_id"),
        CheckConstraint("action ~ '^[a-z][a-z_]*\\.[a-z][a-z_]*$'", name="action_format"),
        CheckConstraint(
            "(target_type IS NULL) = (target_id IS NULL)", name="target_type_iff_target_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(Text)  # noun.verb_past
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class AuditEventNetwork(Base):
    """Where an audited request came from; mutable, so account deletion can scrub it."""

    __tablename__ = "audit_event_network"
    __table_args__ = (CheckConstraint("char_length(user_agent) <= 256", name="user_agent_length"),)

    audit_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_events.id", ondelete="RESTRICT"), primary_key=True
    )
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
