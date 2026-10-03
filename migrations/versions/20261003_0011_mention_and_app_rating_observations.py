"""Mention and app-rating observations.

Both are append-only, and an observation must link a scan and a mention/app of the same brand.
Two generic trigger functions are introduced and reused by later migrations:
- serpsense_same_brand_as_scan(ref_table, ref_column, constraint): the row referenced by
  NEW.<ref_column> must belong to the scan's brand;
- serpsense_forbid_identity_change(constraint, columns...): listed columns never change (typed).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db.ddl import append_only_triggers, drop_append_only_triggers

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SAME_BRAND_FUNCTION = """
CREATE FUNCTION serpsense_same_brand_as_scan() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    ref_brand uuid;
    scan_brand uuid;
BEGIN
    EXECUTE format('SELECT brand_id FROM %I WHERE id = $1.%I', TG_ARGV[0], TG_ARGV[1])
        INTO ref_brand USING NEW;
    SELECT brand_id INTO scan_brand FROM scans WHERE id = NEW.scan_id;
    -- "<>": a missing row yields NULL and is left to the foreign keys to report.
    IF ref_brand <> scan_brand THEN
        RAISE EXCEPTION '% and its scan belong to different brands', TG_TABLE_NAME
            USING ERRCODE = 'check_violation', CONSTRAINT = TG_ARGV[2];
    END IF;
    RETURN NEW;
END;
$$
"""

IDENTITY_FUNCTION = """
CREATE FUNCTION serpsense_forbid_identity_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    i integer;
    changed boolean;
BEGIN
    FOR i IN 1 .. TG_NARGS - 1 LOOP
        EXECUTE format('SELECT $1.%1$I IS DISTINCT FROM $2.%1$I', TG_ARGV[i])
            INTO changed USING NEW, OLD;
        IF changed THEN
            RAISE EXCEPTION '%.% cannot change', TG_TABLE_NAME, TG_ARGV[i]
                USING ERRCODE = 'check_violation', CONSTRAINT = TG_ARGV[0];
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$
"""

# table -> (referenced table, referencing column)
OBSERVATIONS = {
    "mention_observations": ("mentions", "mention_id"),
    "app_rating_observations": ("brand_apps", "brand_app_id"),
}
# table -> (pseudo-constraint name, immutable columns)
IDENTITIES = {
    "mentions": (
        "ck_mentions_identity_immutable",
        ("brand_id", "source", "identity_key", "brand_location_id", "brand_app_id", "created_at"),
    ),
    "brand_apps": ("ck_brand_apps_identity_immutable", ("brand_id", "store", "app_id")),
    "brand_locations": ("ck_brand_locations_identity_immutable", ("brand_id", "query")),
}


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "mention_observations",
        sa.Column("mention_id", sa.UUID(), nullable=False),
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=True),
        sa.Column("star_rating", sa.SmallInteger(), nullable=True),
        sa.CheckConstraint("position >= 1", name=op.f("ck_mention_observations_position_positive")),
        sa.CheckConstraint(
            "star_rating BETWEEN 1 AND 5", name=op.f("ck_mention_observations_star_rating_range")
        ),
        _fk("mention_observations", "mention_id", "mentions"),
        _fk("mention_observations", "scan_id", "scans"),
        sa.PrimaryKeyConstraint("mention_id", "scan_id", name=op.f("pk_mention_observations")),
    )
    op.create_index("ix_mention_observations_scan_id", "mention_observations", ["scan_id"])
    op.create_table(
        "app_rating_observations",
        sa.Column("brand_app_id", sa.UUID(), nullable=False),
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("rating_hundredths", sa.SmallInteger(), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "rating_hundredths BETWEEN 100 AND 500",
            name=op.f("ck_app_rating_observations_rating_range"),
        ),
        sa.CheckConstraint(
            "review_count >= 0", name=op.f("ck_app_rating_observations_review_count_non_negative")
        ),
        _fk("app_rating_observations", "brand_app_id", "brand_apps"),
        _fk("app_rating_observations", "scan_id", "scans"),
        sa.PrimaryKeyConstraint("brand_app_id", "scan_id", name=op.f("pk_app_rating_observations")),
    )
    op.create_index("ix_app_rating_observations_scan_id", "app_rating_observations", ["scan_id"])
    op.execute(SAME_BRAND_FUNCTION)
    op.execute(IDENTITY_FUNCTION)
    for table, (target, column) in OBSERVATIONS.items():
        for statement in append_only_triggers(table):
            op.execute(statement)
        op.execute(
            f"CREATE TRIGGER trg_{table}_same_brand BEFORE INSERT ON {table} FOR EACH ROW "
            f"EXECUTE FUNCTION serpsense_same_brand_as_scan('{target}', '{column}', "
            f"'ck_{table}_same_brand')"
        )
    for table, (constraint, columns) in IDENTITIES.items():
        args = ", ".join(f"'{name}'" for name in (constraint, *columns))
        op.execute(
            f"CREATE TRIGGER trg_{table}_identity_immutable BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION serpsense_forbid_identity_change({args})"
        )


def downgrade() -> None:
    for table in IDENTITIES:
        op.execute(f"DROP TRIGGER trg_{table}_identity_immutable ON {table}")
    for table in OBSERVATIONS:
        op.execute(f"DROP TRIGGER trg_{table}_same_brand ON {table}")
        for statement in drop_append_only_triggers(table):
            op.execute(statement)
    op.execute("DROP FUNCTION serpsense_forbid_identity_change()")
    op.execute("DROP FUNCTION serpsense_same_brand_as_scan()")
    op.drop_index("ix_app_rating_observations_scan_id", table_name="app_rating_observations")
    op.drop_table("app_rating_observations")
    op.drop_index("ix_mention_observations_scan_id", table_name="mention_observations")
    op.drop_table("mention_observations")
