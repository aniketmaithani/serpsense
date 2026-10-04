"""Access requests and the operator's decisions on them (data-model §1, ADR-0014)."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import AccessDecision


class AccessRequest(Base):
    """Mutable: the address is pseudonymised when its account is deleted (ADR-0013)."""

    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    requested_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class AccessRequestDecision(Base):
    """Append-only (database trigger): a new decision is a new row; the latest one counts."""

    __tablename__ = "access_decisions"
    # One decision per request per instant, so "the latest" always has a single answer.
    __table_args__ = (UniqueConstraint("access_request_id", "decided_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    access_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("access_requests.id", ondelete="RESTRICT")
    )
    decision: Mapped[AccessDecision] = mapped_column(pg_enum(AccessDecision, "access_decision"))
    decided_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
