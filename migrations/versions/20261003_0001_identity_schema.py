"""Identity schema, part 1: users, OTP codes and OTP verification attempts.

Also installs `citext` and the reusable append-only trigger function used by every
append-only table (docs/architecture/data-model.md conventions).

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db.ddl import (
    CREATE_FORBID_MUTATION_FUNCTION,
    DROP_FORBID_MUTATION_FUNCTION,
    append_only_triggers,
    drop_append_only_triggers,
)

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _append_only(table: str) -> None:
    for statement in append_only_triggers(table):
        op.execute(statement)


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[sa.DateTime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute(CREATE_FORBID_MUTATION_FUNCTION)

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        _timestamp("created_at"),
        _timestamp("deleted_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "otp_codes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(), nullable=False),
        _timestamp("created_at"),
        _timestamp("expires_at"),
        _timestamp("consumed_at", nullable=True),
        _timestamp("superseded_at", nullable=True),
        sa.Column("request_ip", postgresql.INET(), nullable=True),
        sa.CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND superseded_at IS NOT NULL)",
            name=op.f("ck_otp_codes_single_terminal"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_otp_codes_expires_after_created")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_otp_codes")),
    )
    op.create_index("ix_otp_codes_email_created_at", "otp_codes", ["email", "created_at"])
    op.create_index(
        "uq_otp_codes_one_live_per_email",
        "otp_codes",
        ["email"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND superseded_at IS NULL"),
    )

    op.create_table(
        "otp_verify_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("otp_code_id", sa.UUID(), nullable=False),
        _timestamp("attempted_at"),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["otp_code_id"],
            ["otp_codes.id"],
            name=op.f("fk_otp_verify_attempts_otp_code_id_otp_codes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_otp_verify_attempts")),
    )
    op.create_index(
        op.f("ix_otp_verify_attempts_otp_code_id"), "otp_verify_attempts", ["otp_code_id"]
    )
    op.create_index(
        "uq_otp_verify_attempts_one_success_per_code",
        "otp_verify_attempts",
        ["otp_code_id"],
        unique=True,
        postgresql_where=sa.text("succeeded"),
    )
    _append_only("otp_verify_attempts")


def downgrade() -> None:
    for statement in drop_append_only_triggers("otp_verify_attempts"):
        op.execute(statement)
    op.drop_index("uq_otp_verify_attempts_one_success_per_code", table_name="otp_verify_attempts")
    op.drop_index(op.f("ix_otp_verify_attempts_otp_code_id"), table_name="otp_verify_attempts")
    op.drop_table("otp_verify_attempts")
    op.drop_index("uq_otp_codes_one_live_per_email", table_name="otp_codes")
    op.drop_index("ix_otp_codes_email_created_at", table_name="otp_codes")
    op.drop_table("otp_codes")
    op.drop_table("users")
    op.execute(DROP_FORBID_MUTATION_FUNCTION)
    # citext is left installed: it may have existed before this migration, and it is harmless.
