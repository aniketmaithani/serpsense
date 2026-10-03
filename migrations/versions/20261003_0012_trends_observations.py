"""Google Trends observations.

Trends interest is relative within one query, so each scan stores one joint query: its brand
plus up to 4 competitors. A row's subject must be the scan's brand or one of its competitors
when it is inserted (the comparison stays valid if a competitor is unlinked later), and a
comparison has at most 5 lines. Points are timestamps, so hourly ranges fit too.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "trends_observations"
SUBJECT_FUNCTION = """
CREATE FUNCTION serpsense_trends_subject_in_comparison() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    scan_brand uuid;
BEGIN
    SELECT brand_id INTO scan_brand FROM scans WHERE id = NEW.scan_id;
    -- A missing scan or subject brand is left to its foreign key to report.
    IF scan_brand IS NULL OR NOT EXISTS (SELECT 1 FROM brands WHERE id = NEW.subject_brand_id) THEN
        RETURN NEW;
    END IF;
    IF NEW.subject_brand_id <> scan_brand
       AND NOT EXISTS (
           SELECT 1 FROM brand_competitors
           WHERE brand_id = scan_brand AND competitor_brand_id = NEW.subject_brand_id
       ) THEN
        RAISE EXCEPTION 'trends subject must be the scan''s brand or one of its competitors'
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'ck_trends_observations_subject_in_comparison';
    END IF;
    -- One joint query compares at most 5 lines: the brand and 4 competitors.
    IF (SELECT count(DISTINCT subject_brand_id) FROM trends_observations
        WHERE scan_id = NEW.scan_id AND subject_brand_id <> NEW.subject_brand_id) >= 5 THEN
        RAISE EXCEPTION 'a Trends comparison has at most 5 lines'
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'ck_trends_observations_comparison_size';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("subject_brand_id", sa.UUID(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interest", sa.SmallInteger(), nullable=False),
        sa.Column("is_partial", sa.Boolean(), nullable=False),
        sa.CheckConstraint("interest BETWEEN 0 AND 100", name=op.f(f"ck_{TABLE}_interest_range")),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f(f"fk_{TABLE}_scan_id_scans"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["subject_brand_id"],
            ["brands.id"],
            name=op.f(f"fk_{TABLE}_subject_brand_id_brands"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "scan_id", "subject_brand_id", "observed_at", name=op.f(f"pk_{TABLE}")
        ),
    )
    for statement in append_only_triggers(TABLE):
        op.execute(statement)
    op.execute(SUBJECT_FUNCTION)
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_subject_in_comparison BEFORE INSERT ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_trends_subject_in_comparison()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER trg_{TABLE}_subject_in_comparison ON {TABLE}")
    op.execute("DROP FUNCTION serpsense_trends_subject_in_comparison()")
    for statement in drop_append_only_triggers(TABLE):
        op.execute(statement)
    op.drop_table(TABLE)
