"""Scoring reference data: the weights, level thresholds and warm-up of each scoring version.

Seeded here for `s1` with the numbers in `domain/scoring/` (docs/scoring.md). Append-only: a
version never changes; a new one is a new seed migration. A scan's scores come in 0021.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
COMPONENTS = ("velocity", "spread", "autocomplete", "trends", "press")
LEVELS = ("low", "medium", "high")
REFERENCE = ("scoring_versions", "scoring_weights", "crisis_level_thresholds")
S1_HEALTH = {"search_page": 2500, "autocomplete": 2000, "ai_overview": 1500, "news": 1500}
S1_HEALTH |= {"play": 1500, "maps": 1000}
S1_CRISIS = {"velocity": 3000, "spread": 2500, "autocomplete": 2000, "trends": 1500, "press": 1000}
S1_LEVELS = {"high": 70, "medium": 40, "low": 0}


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _fk(table: str, column: str, target: str, target_column: str = "id") -> sa.ForeignKeyConstraint:
    name = op.f(f"fk_{table}_{column}_{target}")
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.{target_column}"], name=name, ondelete="RESTRICT"
    )


def _reference() -> None:
    op.create_table(
        "scoring_versions",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("warm_up_scans", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version ~ '^s[0-9]{1,3}$'", name=op.f("ck_scoring_versions_version_format")
        ),
        sa.CheckConstraint(
            "warm_up_scans >= 0", name=op.f("ck_scoring_versions_warm_up_scans_non_negative")
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_scoring_versions")),
    )
    op.create_table(
        "scoring_weights",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("kind", sa.Enum("health", "crisis", name="score_kind"), nullable=False),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("weight_bp", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            f"(kind = 'health' AND component IN ({_in(SURFACES)})) "
            f"OR (kind = 'crisis' AND component IN ({_in(COMPONENTS)}))",
            name=op.f("ck_scoring_weights_component_of_kind"),
        ),
        sa.CheckConstraint(
            "weight_bp BETWEEN 1 AND 10000", name=op.f("ck_scoring_weights_weight_bp_range")
        ),
        _fk("scoring_weights", "version", "scoring_versions", "version"),
        sa.PrimaryKeyConstraint("version", "kind", "component", name=op.f("pk_scoring_weights")),
    )
    op.create_table(
        "crisis_level_thresholds",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("level", sa.Enum(*LEVELS, name="crisis_level"), nullable=False),
        sa.Column("min_score", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "min_score BETWEEN 0 AND 100", name=op.f("ck_crisis_level_thresholds_min_score_range")
        ),
        _fk("crisis_level_thresholds", "version", "scoring_versions", "version"),
        sa.PrimaryKeyConstraint("version", "level", name=op.f("pk_crisis_level_thresholds")),
        sa.UniqueConstraint(
            "version", "min_score", name=op.f("uq_crisis_level_thresholds_version_min_score")
        ),
    )


def _seed_s1() -> None:
    created = datetime(2026, 10, 3, tzinfo=UTC)
    versions = sa.table(
        "scoring_versions",
        sa.column("version", sa.Text()),
        sa.column("warm_up_scans", sa.SmallInteger()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(versions, [{"version": "s1", "warm_up_scans": 3, "created_at": created}])
    weights = sa.table(
        "scoring_weights",
        sa.column("version", sa.Text()),
        sa.column(
            "kind", postgresql.ENUM("health", "crisis", name="score_kind", create_type=False)
        ),
        sa.column("component", sa.Text()),
        sa.column("weight_bp", sa.SmallInteger()),
    )
    rows = [("health", c, w) for c, w in S1_HEALTH.items()]
    rows += [("crisis", c, w) for c, w in S1_CRISIS.items()]
    op.bulk_insert(
        weights,
        [{"version": "s1", "kind": k, "component": c, "weight_bp": w} for k, c, w in rows],
    )
    thresholds = sa.table(
        "crisis_level_thresholds",
        sa.column("version", sa.Text()),
        sa.column("level", postgresql.ENUM(*LEVELS, name="crisis_level", create_type=False)),
        sa.column("min_score", sa.SmallInteger()),
    )
    op.bulk_insert(
        thresholds,
        [{"version": "s1", "level": lvl, "min_score": f} for lvl, f in S1_LEVELS.items()],
    )


def upgrade() -> None:
    _reference()
    _seed_s1()
    for table in REFERENCE:
        for statement in append_only_triggers(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(REFERENCE):
        for statement in drop_append_only_triggers(table):
            op.execute(statement)
        op.drop_table(table)
    for enum in ("crisis_level", "score_kind"):
        op.execute(f"DROP TYPE {enum}")
