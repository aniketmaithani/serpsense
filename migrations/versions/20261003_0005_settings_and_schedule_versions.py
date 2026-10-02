"""Versioned settings and brand schedules.

All four tables are append-only (a change is a new row) with one version per key per instant.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Versioned JSON documents: table -> (key column, referenced table).
DOCUMENT_TABLES = {
    "user_llm_profile_versions": ("user_id", "users"),
    "user_search_default_versions": ("user_id", "users"),
    "brand_search_settings_versions": ("brand_id", "brands"),
}
SCHEDULES = "brand_schedule_versions"


def _versioned(table: str, key: str, target: str, *items: Any) -> None:
    """Create a version table (id, key FK, ..., created_at) and make it append-only."""
    op.create_table(
        table,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(key, sa.UUID(), nullable=False),
        *items,
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            [key], [f"{target}.id"], name=op.f(f"fk_{table}_{key}_{target}"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        sa.UniqueConstraint(key, "created_at", name=op.f(f"uq_{table}_{key}_created_at")),
    )
    for statement in append_only_triggers(table):
        op.execute(statement)


def upgrade() -> None:
    for table, (key, target) in DOCUMENT_TABLES.items():
        _versioned(
            table,
            key,
            target,
            sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("schema_version", sa.SmallInteger(), nullable=False),
            sa.CheckConstraint(
                "jsonb_typeof(document) = 'object'", name=op.f(f"ck_{table}_document_is_object")
            ),
            sa.CheckConstraint(
                "schema_version >= 1", name=op.f(f"ck_{table}_schema_version_positive")
            ),
        )
    _versioned(
        SCHEDULES,
        "brand_id",
        "brands",
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("quiet_start", sa.Time(), nullable=True),
        sa.Column("quiet_end", sa.Time(), nullable=True),
        sa.Column("timezone", sa.Text(), server_default=sa.text("'Asia/Kolkata'"), nullable=False),
        sa.CheckConstraint(
            "interval_minutes IN (60, 180, 360, 720, 1440)",
            name=op.f(f"ck_{SCHEDULES}_interval_allowed"),
        ),
        sa.CheckConstraint(
            "(quiet_start IS NULL AND quiet_end IS NULL) OR "
            "(quiet_start IS NOT NULL AND quiet_end IS NOT NULL AND quiet_start <> quiet_end)",
            name=op.f(f"ck_{SCHEDULES}_quiet_hours_valid"),
        ),
        sa.CheckConstraint(
            "timezone ~ '^\\S{1,64}$'", name=op.f(f"ck_{SCHEDULES}_timezone_format")
        ),
    )


def downgrade() -> None:
    for table in [SCHEDULES, *reversed(DOCUMENT_TABLES)]:
        for statement in drop_append_only_triggers(table):
            op.execute(statement)
        op.drop_table(table)
