"""A brand's crisis tuning, and v_scan_scores reading it.

`brand_crisis_tuning_versions` is append-only like the other settings versions: a change is a new
row, the latest per brand is current. `v_scan_scores` takes the warm-up and level floors from the
brand's latest tuning, falling back to its scoring version's (0020), so every scan of a brand is
read with one set of cut-offs: the page and the alert rules compare like with like. The crisis
score itself still comes from the version's weights.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "brand_crisis_tuning_versions"
CHECKS = {
    "warm_up_scans_range": "warm_up_scans BETWEEN 0 AND 8",
    "medium_at_range": "medium_at BETWEEN 1 AND 99",
    "high_above_medium": "high_at > medium_at AND high_at <= 100",
    "cooldown_hours_range": "cooldown_hours BETWEEN 1 AND 72",
    "spread_mentions_range": "spread_mentions BETWEEN 2 AND 50",
    "spread_surfaces_range": "spread_surfaces BETWEEN 1 AND 5",
}
KNOBS = (
    "warm_up_scans",
    "medium_at",
    "high_at",
    "cooldown_hours",
    "spread_mentions",
    "spread_surfaces",
)

# The same columns as 0021's view; only the level's warm-up and floors change.
VIEW = """
CREATE OR REPLACE VIEW v_scan_scores AS
SELECT r.scan_id, s.brand_id, s.created_at, r.version, h.health, c.crisis,
       CASE WHEN e.scans >= coalesce(k.warm_up_scans, v.warm_up_scans) THEN
           CASE WHEN k.high_at IS NULL THEN (
               SELECT t.level FROM crisis_level_thresholds t
               WHERE t.version = r.version AND t.min_score <= c.crisis
               ORDER BY t.min_score DESC LIMIT 1
           )
           WHEN c.crisis >= k.high_at THEN 'high'::crisis_level
           WHEN c.crisis >= k.medium_at THEN 'medium'::crisis_level
           ELSE 'low'::crisis_level END
       END AS crisis_level
FROM score_runs r
JOIN scans s ON s.id = r.scan_id
JOIN scoring_versions v ON v.version = r.version
LEFT JOIN LATERAL (
    SELECT t.warm_up_scans, t.medium_at, t.high_at FROM brand_crisis_tuning_versions t
    WHERE t.brand_id = s.brand_id ORDER BY t.created_at DESC LIMIT 1
) k ON true
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
        LIMIT coalesce(k.warm_up_scans, v.warm_up_scans)
    ) earlier
) e
"""
PREVIOUS_VIEW = """
CREATE OR REPLACE VIEW v_scan_scores AS
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


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        *(sa.Column(knob, sa.SmallInteger(), nullable=False) for knob in KNOBS),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(sql, name=op.f(f"ck_{TABLE}_{name}")) for name, sql in CHECKS.items()),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["brands.id"],
            name=op.f(f"fk_{TABLE}_brand_id_brands"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("brand_id", "created_at", name=op.f(f"uq_{TABLE}_brand_id_created_at")),
    )
    for statement in append_only_triggers(TABLE):
        op.execute(statement)
    op.execute(VIEW)


def downgrade() -> None:
    op.execute(PREVIOUS_VIEW)
    for statement in drop_append_only_triggers(TABLE):
        op.execute(statement)
    op.drop_table(TABLE)
