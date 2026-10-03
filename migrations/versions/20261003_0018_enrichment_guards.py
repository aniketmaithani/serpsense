"""Enrichment guards: provenance and immutability.

- The labels come from a successful call, with the same prompt version, made for the owner of the
  mention's brand (trg_enrichments_from_call → ck_enrichments_from_call), through a generic
  function the narrative tables reuse.
- The table is mutable only so a redaction scrub can rewrite `reason`: every other column is
  frozen (ck_enrichments_identity_immutable) and rows are never deleted.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

from serpsense.adapters.db.ddl import FORBID_MUTATION_FUNCTION

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "enrichments"
IMMUTABLE = (
    "id",
    "mention_id",
    "revision",
    "prompt_version",
    "llm_call_id",
    "sentiment",
    "severity",
    "topic",
    "is_complaint",
    "is_about_brand",
    "created_at",
)

# serpsense_output_from_call(constraint, brand_from): brand_from is 'mention' (the row's
# mention_id names the brand) or 'brand' (the row's brand_id). Columns are read through
# to_jsonb(NEW), so one function serves tables with different columns.
FROM_CALL_FUNCTION = """
CREATE FUNCTION serpsense_output_from_call() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    fields jsonb := to_jsonb(NEW);
    call_outcome llm_call_outcome;
    call_prompt text;
    call_user uuid;
    row_brand uuid;
    brand_owner uuid;
BEGIN
    SELECT outcome, prompt_version, user_id INTO call_outcome, call_prompt, call_user
    FROM llm_calls WHERE id = (fields ->> 'llm_call_id')::uuid;
    IF NOT FOUND THEN
        RETURN NEW;  -- a missing call is left to the foreign key to report
    END IF;
    IF NOT (TG_ARGV[1] = 'mention' AND fields ? 'mention_id'
            OR TG_ARGV[1] = 'brand' AND fields ? 'brand_id') THEN
        RAISE EXCEPTION 'serpsense_output_from_call: % has no %_id column',
            TG_TABLE_NAME, TG_ARGV[1];
    END IF;
    IF TG_ARGV[1] = 'mention' THEN
        SELECT brand_id INTO row_brand FROM mentions WHERE id = (fields ->> 'mention_id')::uuid;
    ELSE
        row_brand := (fields ->> 'brand_id')::uuid;
    END IF;
    SELECT owner_id INTO brand_owner FROM brands WHERE id = row_brand;
    IF call_outcome <> 'succeeded' OR call_prompt <> fields ->> 'prompt_version'
        OR brand_owner <> call_user THEN
        RAISE EXCEPTION 'model output comes from a successful call for the brand''s owner'
            USING ERRCODE = 'check_violation', CONSTRAINT = TG_ARGV[0];
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.execute(FROM_CALL_FUNCTION)
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_from_call BEFORE INSERT ON {TABLE} FOR EACH ROW "
        f"EXECUTE FUNCTION serpsense_output_from_call('ck_{TABLE}_from_call', 'mention')"
    )
    args = ", ".join(f"'{name}'" for name in (f"ck_{TABLE}_identity_immutable", *IMMUTABLE))
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_identity_immutable BEFORE UPDATE ON {TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION serpsense_forbid_identity_change({args})"
    )
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_no_delete BEFORE DELETE ON {TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {FORBID_MUTATION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_no_truncate BEFORE TRUNCATE ON {TABLE} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {FORBID_MUTATION_FUNCTION}()"
    )


def downgrade() -> None:
    for trigger in ("no_truncate", "no_delete", "identity_immutable", "from_call"):
        op.execute(f"DROP TRIGGER trg_{TABLE}_{trigger} ON {TABLE}")
    op.execute("DROP FUNCTION serpsense_output_from_call()")
