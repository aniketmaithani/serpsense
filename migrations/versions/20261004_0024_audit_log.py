"""The audit log: append-only events, and their network details kept apart.

An event records who did what to what, and when, with optional non-sensitive details; never an
email, IP, code or token (ADR-0009). Where the request came from lives in audit_event_network,
which is mutable so account deletion can scrub it (ADR-0013).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db import ddl

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS, NETWORK = "audit_events", "audit_event_network"


def upgrade() -> None:
    op.create_table(
        EVENTS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action ~ '^[a-z][a-z_]*\\.[a-z][a-z_]*$'", name=op.f(f"ck_{EVENTS}_action_format")
        ),
        sa.CheckConstraint(
            "(target_type IS NULL) = (target_id IS NULL)",
            name=op.f(f"ck_{EVENTS}_target_type_iff_target_id"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f(f"fk_{EVENTS}_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{EVENTS}")),
    )
    op.create_index(
        f"ix_{EVENTS}_actor_user_id_created_at", EVENTS, ["actor_user_id", "created_at"]
    )
    op.create_index(f"ix_{EVENTS}_target_id", EVENTS, ["target_id"])
    for statement in ddl.append_only_triggers(EVENTS):
        op.execute(statement)
    op.create_table(
        NETWORK,
        sa.Column("audit_event_id", sa.UUID(), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "char_length(user_agent) <= 256", name=op.f(f"ck_{NETWORK}_user_agent_length")
        ),
        sa.ForeignKeyConstraint(
            ["audit_event_id"],
            [f"{EVENTS}.id"],
            name=op.f(f"fk_{NETWORK}_audit_event_id_{EVENTS}"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("audit_event_id", name=op.f(f"pk_{NETWORK}")),
    )


def downgrade() -> None:
    op.drop_table(NETWORK)
    for statement in ddl.drop_append_only_triggers(EVENTS):
        op.execute(statement)
    op.drop_table(EVENTS)
