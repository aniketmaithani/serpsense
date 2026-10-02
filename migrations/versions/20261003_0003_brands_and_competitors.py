"""Brands and competitors.

A brand and its competitor must share an owner, and a brand's owner never changes; both rules
are enforced by triggers because they span rows.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SAME_OWNER_FUNCTION = """
CREATE FUNCTION serpsense_brand_competitor_same_owner() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT owner_id FROM brands WHERE id = NEW.brand_id)
       IS DISTINCT FROM (SELECT owner_id FROM brands WHERE id = NEW.competitor_brand_id) THEN
        RAISE EXCEPTION 'brand and competitor must have the same owner'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_brand_competitors_same_owner';
    END IF;
    RETURN NEW;
END;
$$
"""

OWNER_IMMUTABLE_FUNCTION = """
CREATE FUNCTION serpsense_brand_owner_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
        RAISE EXCEPTION 'a brand''s owner cannot change'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_brands_owner_immutable';
    END IF;
    RETURN NEW;
END;
$$
"""


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("tone_notes", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("archived_at", nullable=True),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120", name=op.f("ck_brands_name_length")
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) <= 64",
            name=op.f("ck_brands_slug_format"),
        ),
        sa.CheckConstraint(
            "char_length(tone_notes) <= 2000", name=op.f("ck_brands_tone_notes_length")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_brands_owner_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brands")),
        sa.UniqueConstraint("owner_id", "slug", name=op.f("uq_brands_owner_id_slug")),
    )
    op.execute(OWNER_IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_brands_owner_immutable BEFORE UPDATE OF owner_id ON brands "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_brand_owner_immutable()"
    )

    op.create_table(
        "brand_competitors",
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("competitor_brand_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "brand_id <> competitor_brand_id", name=op.f("ck_brand_competitors_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["brands.id"],
            name=op.f("fk_brand_competitors_brand_id_brands"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["competitor_brand_id"],
            ["brands.id"],
            name=op.f("fk_brand_competitors_competitor_brand_id_brands"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "brand_id", "competitor_brand_id", name=op.f("pk_brand_competitors")
        ),
    )
    op.create_index(
        "ix_brand_competitors_competitor_brand_id", "brand_competitors", ["competitor_brand_id"]
    )
    op.execute(SAME_OWNER_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_brand_competitors_same_owner BEFORE INSERT OR UPDATE "
        "ON brand_competitors FOR EACH ROW EXECUTE FUNCTION serpsense_brand_competitor_same_owner()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_brand_competitors_same_owner ON brand_competitors")
    op.execute("DROP FUNCTION serpsense_brand_competitor_same_owner()")
    op.drop_index("ix_brand_competitors_competitor_brand_id", table_name="brand_competitors")
    op.drop_table("brand_competitors")
    op.execute("DROP TRIGGER trg_brands_owner_immutable ON brands")
    op.execute("DROP FUNCTION serpsense_brand_owner_immutable()")
    op.drop_table("brands")
