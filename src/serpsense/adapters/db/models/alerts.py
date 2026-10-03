"""Alerts and in-app notifications (docs/architecture/data-model.md §8). All append-only."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import AlertRule


def _fk(target: str, *, nullable: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{target}.id", ondelete="RESTRICT"), nullable=nullable
    )


class Alert(Base):
    """A deterministic rule that fired for a scan; its brand is the scan's. Written once per
    (scan, rule, narrative), so re-running a scan's alert step can't duplicate it. A narrative
    alert's narrative is of the scan's brand (trigger, migration 0022)."""

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("scan_id", "rule", "narrative_id", postgresql_nulls_not_distinct=True),
        Index("ix_alerts_narrative_id", "narrative_id"),
        CheckConstraint(
            "(rule = 'narrative_spread') = (narrative_id IS NOT NULL)", name="narrative_iff_spread"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    scan_id: Mapped[uuid.UUID] = _fk("scans")
    narrative_id: Mapped[uuid.UUID | None] = _fk("narratives", nullable=True)
    rule: Mapped[AlertRule] = mapped_column(pg_enum(AlertRule, "alert_rule"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class Notification(Base):
    """An in-app notification, written in the same transaction as what caused it. One about an
    alert goes to the owner of the alert's brand, once (trigger, migration 0022)."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("alert_id", "user_id"),
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
        CheckConstraint("title ~ '\\S' AND char_length(title) <= 200", name="title_length"),
        CheckConstraint("body ~ '\\S' AND char_length(body) <= 2000", name="body_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _fk("users")
    alert_id: Mapped[uuid.UUID | None] = _fk("alerts", nullable=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class NotificationRead(Base):
    """When a notification was first read; unread is the absence of a row."""

    __tablename__ = "notification_reads"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="RESTRICT"), primary_key=True
    )
    read_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
