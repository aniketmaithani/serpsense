"""A brand has at most four competitors (owner decision, 2026-10-03).

Google Trends compares at most five lines in one query, so with four competitors every one of
them is always in the brand's joint query. The trigger locks the brand row first, so two
concurrent links can't both see three.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAP_FUNCTION = """
CREATE FUNCTION serpsense_brand_competitors_at_most_four() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.brand_id = OLD.brand_id THEN
        RETURN NEW;  -- the brand keeps the same number of competitors
    END IF;
    IF EXISTS (
        SELECT 1 FROM brand_competitors
        WHERE brand_id = NEW.brand_id AND competitor_brand_id = NEW.competitor_brand_id
    ) THEN
        RETURN NEW;  -- an existing link: the primary key (or ON CONFLICT DO NOTHING) decides
    END IF;
    -- A missing brand locks nothing and counts zero, so its foreign key reports it.
    PERFORM 1 FROM brands WHERE id = NEW.brand_id FOR NO KEY UPDATE;
    IF (SELECT count(*) FROM brand_competitors WHERE brand_id = NEW.brand_id) >= 4 THEN
        RAISE EXCEPTION 'a brand has at most 4 competitors'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_brand_competitors_at_most_four';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.execute(CAP_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_brand_competitors_at_most_four "
        "BEFORE INSERT OR UPDATE OF brand_id ON brand_competitors "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_brand_competitors_at_most_four()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_brand_competitors_at_most_four ON brand_competitors")
    op.execute("DROP FUNCTION serpsense_brand_competitors_at_most_four()")
