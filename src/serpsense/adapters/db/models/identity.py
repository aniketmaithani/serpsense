"""Identity & access tables (docs/architecture/data-model.md §1)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import Base

TIMESTAMPTZ = DateTime(timezone=True)


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))


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


class UserSession(Base):
    """A login session (table `sessions`); named to avoid clashing with sqlalchemy.orm.Session."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        CheckConstraint("char_length(user_agent) <= 256", name="user_agent_length"),
        Index("ix_sessions_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    # SHA-256 of the cookie token; the token itself is never stored.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    csrf_secret: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class UserSearchBudget(Base):
    """Append-only: a change is a new row.

    The current budget is the latest effective_from <= now; at most one row per instant.
    """

    __tablename__ = "user_search_budgets"
    __table_args__ = (
        CheckConstraint("monthly_searches >= 0", name="monthly_searches_non_negative"),
        UniqueConstraint("user_id", "effective_from"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    monthly_searches: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class UserLlmBudget(Base):
    """Append-only, like UserSearchBudget.

    Money as integer micros plus an ISO 4217 currency code (AGENTS.md §3).
    """

    __tablename__ = "user_llm_budgets"
    __table_args__ = (
        CheckConstraint("monthly_micros >= 0", name="monthly_micros_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso_4217"),
        UniqueConstraint("user_id", "effective_from"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    monthly_micros: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(CHAR(3))
    effective_from: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
