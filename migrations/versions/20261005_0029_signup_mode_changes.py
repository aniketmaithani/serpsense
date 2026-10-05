"""The operator's switches of the sign-up mode (ADR-0015).

`signup_mode_changes` is append-only: a switch is a new row, the latest counts, and with none the
environment's SIGNUP_MODE applies. One per instant (uq_signup_mode_changes_changed_at), so the
latest is always one row.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db import ddl

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "signup_mode_changes"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.Enum("open", "invite", name="signup_mode"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("changed_at", name=op.f(f"uq_{TABLE}_changed_at")),
    )
    for statement in ddl.append_only_triggers(TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in ddl.drop_append_only_triggers(TABLE):
        op.execute(statement)
    op.drop_table(TABLE)
    op.execute("DROP TYPE signup_mode")
