"""Identity schema, part 2: sessions and per-user budgets.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUDGET_TABLES = ("user_search_budgets", "user_llm_budgets")


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _user_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="RESTRICT"
    )


def _budget_table(table: str, *columns: sa.Column[Any], checks: dict[str, str]) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        *columns,
        _timestamp("effective_from"),
        _timestamp("created_at"),
        *[sa.CheckConstraint(sql, name=op.f(f"ck_{table}_{name}")) for name, sql in checks.items()],
        _user_fk(table),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        sa.UniqueConstraint(
            "user_id", "effective_from", name=op.f(f"uq_{table}_user_id_effective_from")
        ),
    )
    for statement in append_only_triggers(table):
        op.execute(statement)


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("csrf_secret", sa.LargeBinary(), nullable=False),
        _timestamp("created_at"),
        _timestamp("expires_at"),
        _timestamp("revoked_at", nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "char_length(user_agent) <= 256", name=op.f("ck_sessions_user_agent_length")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_sessions_expires_after_created")
        ),
        _user_fk("sessions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    _budget_table(
        "user_search_budgets",
        sa.Column("monthly_searches", sa.Integer(), nullable=False),
        checks={"monthly_searches_non_negative": "monthly_searches >= 0"},
    )
    _budget_table(
        "user_llm_budgets",
        sa.Column("monthly_micros", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        checks={
            "monthly_micros_non_negative": "monthly_micros >= 0",
            "currency_iso_4217": "currency ~ '^[A-Z]{3}$'",
        },
    )


def downgrade() -> None:
    for table in reversed(BUDGET_TABLES):
        for statement in drop_append_only_triggers(table):
            op.execute(statement)
        op.drop_table(table)
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
