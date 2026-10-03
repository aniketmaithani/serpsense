"""A scan's scores: its results, and v_scan_scores deriving health, crisis and level.

A scored scan has a score run, a score per surface that showed something, and its crisis
components. Append-only: a scan is scored once. `v_scan_scores` derives health, the crisis score
and the crisis level from them and the version's reference rows (0020), the way `domain.scoring`
computes them; an integration test keeps the two equal. It computes each row on its own
(LATERAL), so reading a brand's latest scans costs only their rows.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SURFACES = ("search_page", "ai_overview", "autocomplete", "news", "trends", "play", "maps")
SURFACES += ("youtube",)
COMPONENTS = ("velocity", "spread", "autocomplete", "trends", "press")
RESULTS = ("score_runs", "surface_scores", "crisis_components")

# Weights are basis points, multiplied as integers: smallint products overflow. The warm-up
# counts earlier scored scans (succeeded or partial) of the brand, by creation time.
V_SCAN_SCORES = """
CREATE VIEW v_scan_scores AS
SELECT r.scan_id, s.brand_id, s.created_at, r.version, h.health, c.crisis,
       CASE WHEN e.scans >= v.warm_up_scans THEN (
           SELECT t.level FROM crisis_level_thresholds t
           WHERE t.version = r.version AND t.min_score <= c.crisis
           ORDER BY t.min_score DESC LIMIT 1
       ) END AS crisis_level
FROM score_runs r
JOIN scans s ON s.id = r.scan_id
JOIN scoring_versions v ON v.version = r.version
CROSS JOIN LATERAL (
    SELECT round(sum(w.weight_bp::integer * x.score)::numeric / sum(w.weight_bp))::smallint
           AS health
    FROM surface_scores x
    JOIN scoring_weights w
      ON w.version = r.version AND w.kind = 'health' AND w.component = x.surface::text
    WHERE x.scan_id = r.scan_id
) h
CROSS JOIN LATERAL (
    SELECT round(coalesce(sum(w.weight_bp::integer * x.value), 0)::numeric / 10000)::smallint
           AS crisis
    FROM crisis_components x
    JOIN scoring_weights w
      ON w.version = r.version AND w.kind = 'crisis' AND w.component = x.component::text
    WHERE x.scan_id = r.scan_id
) c
CROSS JOIN LATERAL (
    SELECT count(*) AS scans FROM (
        SELECT 1 FROM scans es JOIN score_runs er ON er.scan_id = es.id
        WHERE es.brand_id = s.brand_id AND es.created_at < s.created_at
        LIMIT v.warm_up_scans
    ) earlier
) e
"""


def _fk(table: str, column: str, target: str, target_column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.{target_column}"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        "score_runs",
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        _fk("score_runs", "scan_id", "scans", "id"),
        _fk("score_runs", "version", "scoring_versions", "version"),
        sa.PrimaryKeyConstraint("scan_id", name=op.f("pk_score_runs")),
    )
    surface = postgresql.ENUM(*SURFACES, name="surface", create_type=False)
    op.create_table(
        "surface_scores",
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("surface", surface, nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name=op.f("ck_surface_scores_score_range")),
        _fk("surface_scores", "scan_id", "score_runs", "scan_id"),
        sa.PrimaryKeyConstraint("scan_id", "surface", name=op.f("pk_surface_scores")),
    )
    op.create_table(
        "crisis_components",
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("component", sa.Enum(*COMPONENTS, name="crisis_component"), nullable=False),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "value BETWEEN 0 AND 100", name=op.f("ck_crisis_components_value_range")
        ),
        _fk("crisis_components", "scan_id", "score_runs", "scan_id"),
        sa.PrimaryKeyConstraint("scan_id", "component", name=op.f("pk_crisis_components")),
    )
    for table in RESULTS:
        for statement in append_only_triggers(table):
            op.execute(statement)
    op.execute(V_SCAN_SCORES)


def downgrade() -> None:
    op.execute("DROP VIEW v_scan_scores")
    for table in reversed(RESULTS):
        for statement in drop_append_only_triggers(table):
            op.execute(statement)
        op.drop_table(table)
    op.execute("DROP TYPE crisis_component")
