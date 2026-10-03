"""Alert explanations: append-only model output.

An explanation says in plain words why an alert fired (the model never decides or sends anything,
ADR-0008). It never changes once written: a better one would be a new prompt version's. Guards:
- it comes from a successful call, with its prompt version, made for the owner of the alert's
  brand: the generic serpsense_output_from_call (migration 0018) now also finds a row's brand
  through an alert (its scan's) or a narrative (for drafts, migration 0026),
  → ck_alert_explanations_from_call;
- one explanation per alert (uq_alert_explanations_alert_id), and one per call
  (uq_alert_explanations_llm_call_id): a call explains one alert.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db import ddl

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPLANATIONS = "alert_explanations"


# serpsense_output_from_call (migration 0018), with room for more ways to find the row's brand.
FROM_CALL = """
CREATE OR REPLACE FUNCTION serpsense_output_from_call() RETURNS trigger
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
    IF NOT fields ? (TG_ARGV[1] || '_id') THEN
        RAISE EXCEPTION 'serpsense_output_from_call: % has no %_id column',
            TG_TABLE_NAME, TG_ARGV[1];
    END IF;
    IF TG_ARGV[1] = 'mention' THEN
        SELECT brand_id INTO row_brand FROM mentions WHERE id = (fields ->> 'mention_id')::uuid;
    ELSIF TG_ARGV[1] = 'brand' THEN
        row_brand := (fields ->> 'brand_id')::uuid;-- more ways --
    ELSE
        RAISE EXCEPTION 'serpsense_output_from_call: unknown way to the brand %', TG_ARGV[1];
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
THROUGH_ALERTS_AND_NARRATIVES = """
    ELSIF TG_ARGV[1] = 'alert' THEN
        SELECT s.brand_id INTO row_brand FROM alerts a JOIN scans s ON s.id = a.scan_id
        WHERE a.id = (fields ->> 'alert_id')::uuid;
    ELSIF TG_ARGV[1] = 'narrative' THEN
        SELECT brand_id INTO row_brand FROM narratives
        WHERE id = (fields ->> 'narrative_id')::uuid;"""


def _from_call_function(more: str) -> str:
    return FROM_CALL.replace("-- more ways --", more)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete="RESTRICT"
    )


def _task(table: str, task: str) -> sa.CheckConstraint:
    sql = f"split_part(prompt_version, '/', 1) = '{task}'"
    return sa.CheckConstraint(sql, name=op.f(f"ck_{table}_prompt_from_{task}_task"))


def _text(table: str, column: str, most: int) -> sa.CheckConstraint:
    sql = f"{column} ~ '\\S' AND char_length({column}) <= {most}"
    return sa.CheckConstraint(sql, name=op.f(f"ck_{table}_{column}_length"))


def _from_call(table: str, brand_from: str) -> None:
    op.execute(
        f"CREATE TRIGGER trg_{table}_from_call BEFORE INSERT ON {table} FOR EACH ROW "
        f"EXECUTE FUNCTION serpsense_output_from_call('ck_{table}_from_call', '{brand_from}')"
    )


def _explanations() -> None:
    op.create_table(
        EXPLANATIONS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("alert_id", sa.UUID(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("llm_call_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _text(EXPLANATIONS, "text", 2000),
        _task(EXPLANATIONS, "explain_crisis"),
        _fk(EXPLANATIONS, "alert_id", "alerts"),
        _fk(EXPLANATIONS, "llm_call_id", "llm_calls"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{EXPLANATIONS}")),
        sa.UniqueConstraint("alert_id", name=op.f(f"uq_{EXPLANATIONS}_alert_id")),
        sa.UniqueConstraint("llm_call_id", name=op.f(f"uq_{EXPLANATIONS}_llm_call_id")),
    )
    _from_call(EXPLANATIONS, "alert")


def upgrade() -> None:
    op.execute(_from_call_function(THROUGH_ALERTS_AND_NARRATIVES))
    _explanations()
    for statement in ddl.append_only_triggers(EXPLANATIONS):
        op.execute(statement)


def downgrade() -> None:
    for statement in ddl.drop_append_only_triggers(EXPLANATIONS):
        op.execute(statement)
    op.execute(f"DROP TRIGGER trg_{EXPLANATIONS}_from_call ON {EXPLANATIONS}")
    op.drop_table(EXPLANATIONS)
    op.execute(_from_call_function(""))
