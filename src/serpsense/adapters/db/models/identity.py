"""Identity & access tables (docs/architecture/data-model.md §1)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import Base

TIMESTAMPTZ = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # Pseudonymised to deleted+<id>@serpsense.invalid on account deletion (ADR-0013).
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    # Users are created on first successful OTP verification, so this is also verification time.
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class OtpCode(Base):
    __tablename__ = "otp_codes"
    __table_args__ = (
        CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND superseded_at IS NOT NULL)", name="single_terminal"
        ),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index("ix_otp_codes_email_created_at", "email", "created_at"),
        # At most one live (neither consumed nor superseded) code per email, so concurrent
        # requests can't multiply the guess budget (ADR-0009).
        Index(
            "uq_otp_codes_one_live_per_email",
            "email",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND superseded_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT)
    # HMAC-SHA256(K_otp, email:code); the plaintext code is never stored here.
    code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    superseded_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    request_ip: Mapped[str | None] = mapped_column(INET)


class OtpVerifyAttempt(Base):
    """Append-only (database trigger). No IP is stored; per-IP limits live in Redis."""

    __tablename__ = "otp_verify_attempts"
    __table_args__ = (
        # A code can be verified successfully only once (defence in depth for single use).
        Index(
            "uq_otp_verify_attempts_one_success_per_code",
            "otp_code_id",
            unique=True,
            postgresql_where=text("succeeded"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    otp_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("otp_codes.id", ondelete="RESTRICT"), index=True
    )
    attempted_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    succeeded: Mapped[bool] = mapped_column(Boolean)
