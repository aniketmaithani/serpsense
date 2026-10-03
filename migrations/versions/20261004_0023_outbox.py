"""The email outbox (ADR-0010): messages, and their append-only send attempts.

A message is written in the same transaction as what caused it, with a unique dedupe key; the
dispatcher sends it after commit and records each attempt. `status` is a named exception to "no
derived values" (the dispatcher's claim predicate), written only with an attempt row. Guards:
- an OTP email names its code and an alert email its alert, and nothing else
  (ck_outbox_messages_kind_refs);
- the encrypted payload exists only while the message is pending
  (ck_outbox_messages_sensitive_only_pending);
- the recipient is one bare address, so SMTP can't fan it out (ck_outbox_messages_recipient_plain),
  and an alert email names its user (ck_outbox_messages_alert_email_has_user);
- an attempt's error code is set exactly for an error (ck_outbox_attempts_error_code_iff_error).
`outbox_messages` is mutable so account deletion can scrub the recipient (ADR-0013).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db import ddl

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MESSAGES, ATTEMPTS = "outbox_messages", "outbox_attempts"
KINDS = ("otp_email", "alert_email")
STATUSES = ("pending", "sent", "dead", "dropped")
OUTCOMES = ("sent", "retryable_error", "permanent_error", "dropped")
MESSAGE_CHECKS = {
    "kind_refs": "(kind = 'otp_email') = (otp_code_id IS NOT NULL) "
    "AND (kind = 'alert_email') = (alert_id IS NOT NULL)",
    "sensitive_only_pending": "status = 'pending' OR sensitive_data_encrypted IS NULL",
    "dedupe_key_length": "dedupe_key ~ '\\S' AND char_length(dedupe_key) <= 200",
    # One bare address: no display name or list for SMTP to fan out to.
    "recipient_plain": "recipient_email ~ '^[^@\\s,;<>]+@[^@\\s,;<>]+$'",
    "alert_email_has_user": "kind <> 'alert_email' OR user_id IS NOT NULL",
}
ATTEMPT_CHECKS = {
    "error_code_format": "error_code ~ '^[a-z][a-z0-9_.]{0,63}$'",
    "error_code_iff_error": "(outcome IN ('retryable_error', 'permanent_error')) "
    "= (error_code IS NOT NULL)",
}


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete="RESTRICT"
    )


def _checks(table: str, checks: dict[str, str]) -> list[sa.CheckConstraint]:
    return [sa.CheckConstraint(sql, name=op.f(f"ck_{table}_{n}")) for n, sql in checks.items()]


def upgrade() -> None:
    op.create_table(
        MESSAGES,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Enum(*KINDS, name="outbox_kind"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("otp_code_id", sa.UUID(), nullable=True),
        sa.Column("alert_id", sa.UUID(), nullable=True),
        sa.Column("recipient_email", postgresql.CITEXT(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("template_data", postgresql.JSONB(), nullable=True),
        sa.Column("sensitive_data_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum(*STATUSES, name="outbox_status"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_checks(MESSAGES, MESSAGE_CHECKS),
        _fk(MESSAGES, "user_id", "users"),
        _fk(MESSAGES, "otp_code_id", "otp_codes"),
        _fk(MESSAGES, "alert_id", "alerts"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{MESSAGES}")),
        sa.UniqueConstraint("dedupe_key", name=op.f(f"uq_{MESSAGES}_dedupe_key")),
    )
    op.create_index(
        f"ix_{MESSAGES}_pending",
        MESSAGES,
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(f"ix_{MESSAGES}_user_id", MESSAGES, ["user_id"])  # the deletion scrub
    op.create_table(
        ATTEMPTS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("outbox_message_id", sa.UUID(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.Enum(*OUTCOMES, name="outbox_attempt_outcome"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        *_checks(ATTEMPTS, ATTEMPT_CHECKS),
        _fk(ATTEMPTS, "outbox_message_id", MESSAGES),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ATTEMPTS}")),
    )
    op.create_index(
        f"ix_{ATTEMPTS}_outbox_message_id_attempted_at",
        ATTEMPTS,
        ["outbox_message_id", "attempted_at"],
    )
    for statement in ddl.append_only_triggers(ATTEMPTS):
        op.execute(statement)


def downgrade() -> None:
    for statement in ddl.drop_append_only_triggers(ATTEMPTS):
        op.execute(statement)
    op.drop_table(ATTEMPTS)
    op.drop_table(MESSAGES)
    for enum in ("outbox_attempt_outcome", "outbox_status", "outbox_kind"):
        op.execute(f"DROP TYPE {enum}")
