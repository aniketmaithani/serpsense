"""Access requests and the operator's decisions on them (ADR-0014).

`access_requests` holds the address someone asked for a code with while not invited: mutable, so
account deletion can pseudonymise it (ADR-0013); one row per address (uq_access_requests_email).
`access_decisions` is append-only: approving or rejecting is a new row, and the latest counts;
one per request per instant (uq_access_decisions_access_request_id_decided_at), so the latest is
always one row.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

from serpsense.adapters.db import ddl

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUESTS, DECISIONS = "access_requests", "access_decisions"


def upgrade() -> None:
    op.create_table(
        REQUESTS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", CITEXT(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{REQUESTS}")),
        sa.UniqueConstraint("email", name=op.f(f"uq_{REQUESTS}_email")),
    )
    op.create_table(
        DECISIONS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("access_request_id", sa.UUID(), nullable=False),
        sa.Column(
            "decision", sa.Enum("approved", "rejected", name="access_decision"), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["access_request_id"],
            [f"{REQUESTS}.id"],
            name=op.f(f"fk_{DECISIONS}_access_request_id_{REQUESTS}"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{DECISIONS}")),
        sa.UniqueConstraint(
            "access_request_id",
            "decided_at",
            name=op.f(f"uq_{DECISIONS}_access_request_id_decided_at"),
        ),
    )
    for statement in ddl.append_only_triggers(DECISIONS):
        op.execute(statement)


def downgrade() -> None:
    for statement in ddl.drop_append_only_triggers(DECISIONS):
        op.execute(statement)
    op.drop_table(DECISIONS)
    op.drop_table(REQUESTS)
    op.execute("DROP TYPE access_decision")
