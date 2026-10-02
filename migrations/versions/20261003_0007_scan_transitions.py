"""Scan status transitions.

Append-only. Transitions must follow the scan state machine, which Postgres checks
as defence in depth for domain.scan_state.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRANSITIONS = "scan_status_transitions"
# scan_status already exists (migration 0006); reuse it without re-creating it.
SCAN_STATUS = postgresql.ENUM(name="scan_status", create_type=False)
CODE_FORMAT = "~ '^[a-z][a-z0-9_.]{0,63}$'"


def _check(table: str, name: str, sql: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sql, name=op.f(f"ck_{table}_{name}"))


def upgrade() -> None:
    op.create_table(
        TRANSITIONS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("from_status", SCAN_STATUS, nullable=True),
        sa.Column("to_status", SCAN_STATUS, nullable=False),
        sa.Column("actor", sa.Enum("system", "user", name="transition_actor"), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        _check(
            TRANSITIONS,
            "allowed_transition",
            "CASE"
            " WHEN from_status IS NULL THEN to_status = 'queued'"
            " WHEN from_status = 'queued' THEN to_status IN ('running', 'skipped')"
            " WHEN from_status = 'running'"
            " THEN to_status IN ('succeeded', 'partial', 'failed', 'skipped')"
            " ELSE false END",
        ),
        _check(TRANSITIONS, "actor_user_matches", "(actor = 'user') = (actor_user_id IS NOT NULL)"),
        _check(TRANSITIONS, "reason_format", f"reason {CODE_FORMAT}"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f(f"fk_{TRANSITIONS}_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name=op.f(f"fk_{TRANSITIONS}_scan_id_scans"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TRANSITIONS}")),
        sa.UniqueConstraint(
            "scan_id", "to_status", name=op.f(f"uq_{TRANSITIONS}_scan_id_to_status")
        ),
    )
    op.create_index(f"ix_{TRANSITIONS}_scan_id_at", TRANSITIONS, ["scan_id", "at"])

    for statement in append_only_triggers(TRANSITIONS):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_append_only_triggers(TRANSITIONS):
        op.execute(statement)
    op.drop_index(f"ix_{TRANSITIONS}_scan_id_at", table_name=TRANSITIONS)
    op.drop_table(TRANSITIONS)
    op.execute("DROP TYPE transition_actor")
