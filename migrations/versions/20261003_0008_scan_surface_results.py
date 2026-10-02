"""Per-surface scan results.

Append-only: how collecting each surface went in a scan (drives the "partial scan" UI).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "scan_surface_results"
SURFACES = (
    "search_page",
    "ai_overview",
    "autocomplete",
    "news",
    "trends",
    "play",
    "maps",
    "youtube",
)
OUTCOMES = ("succeeded", "failed", "disabled", "not_shown", "circuit_open", "budget_exhausted")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("surface", sa.Enum(*SURFACES, name="surface"), nullable=False),
        sa.Column("outcome", sa.Enum(*OUTCOMES, name="surface_outcome"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(outcome = 'failed') = (error_code IS NOT NULL)",
            name=op.f(f"ck_{TABLE}_error_code_iff_failed"),
        ),
        sa.CheckConstraint(
            "error_code ~ '^[a-z][a-z0-9_.]{0,63}$'", name=op.f(f"ck_{TABLE}_error_code_format")
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f(f"fk_{TABLE}_scan_id_scans"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("scan_id", "surface", name=op.f(f"pk_{TABLE}")),
    )
    for statement in append_only_triggers(TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_append_only_triggers(TABLE):
        op.execute(statement)
    op.drop_table(TABLE)
    op.execute("DROP TYPE surface_outcome")
    op.execute("DROP TYPE surface")
