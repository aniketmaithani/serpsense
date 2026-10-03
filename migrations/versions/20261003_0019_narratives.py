"""Narratives and narrative assignments.

A narrative is a story the model found in a brand's mentions. It is immutable (append-only): a
re-summarised story is a new narrative, linked through assignments. Assignments are append-only
too, and the latest per mention wins (A → B → A is allowed); one per mention per instant, so
"latest" has a single answer. Guards:
- both come from a successful grouping call, with their prompt version, made for the brand's
  owner (the generic serpsense_output_from_call from migration 0018 → ck_<table>_from_call);
- an assignment links a narrative and a mention of the same brand
  (trg_narrative_assignments_same_brand → ck_narrative_assignments_same_brand).

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db import ddl

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NARRATIVES = "narratives"
ASSIGNMENTS = "narrative_assignments"
GROUPING_PROMPT = "split_part(prompt_version, '/', 1) = 'group_narratives'"
NARRATIVE_CHECKS = {
    "label_length": "label ~ '\\S' AND char_length(label) <= 120",
    "summary_length": "summary ~ '\\S' AND char_length(summary) <= 2000",
    "prompt_from_grouping_task": GROUPING_PROMPT,
}

SAME_BRAND_FUNCTION = """
CREATE FUNCTION serpsense_narrative_assignment_same_brand() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    narrative_brand uuid;
    mention_brand uuid;
BEGIN
    SELECT brand_id INTO narrative_brand FROM narratives WHERE id = NEW.narrative_id;
    SELECT brand_id INTO mention_brand FROM mentions WHERE id = NEW.mention_id;
    -- "<>": a missing narrative or mention is left to its foreign key to report.
    IF narrative_brand <> mention_brand THEN
        RAISE EXCEPTION 'a narrative groups mentions of its own brand'
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'ck_narrative_assignments_same_brand';
    END IF;
    RETURN NEW;
END;
$$
"""


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete="RESTRICT"
    )


def _from_call(table: str, brand_from: str) -> None:
    op.execute(
        f"CREATE TRIGGER trg_{table}_from_call BEFORE INSERT ON {table} FOR EACH ROW "
        f"EXECUTE FUNCTION serpsense_output_from_call('ck_{table}_from_call', '{brand_from}')"
    )


def upgrade() -> None:
    op.create_table(
        NARRATIVES,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("llm_call_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *[
            sa.CheckConstraint(sql, name=op.f(f"ck_{NARRATIVES}_{n}"))
            for n, sql in NARRATIVE_CHECKS.items()
        ],
        _fk(NARRATIVES, "brand_id", "brands"),
        _fk(NARRATIVES, "llm_call_id", "llm_calls"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{NARRATIVES}")),
    )
    op.create_index(f"ix_{NARRATIVES}_brand_id_created_at", NARRATIVES, ["brand_id", "created_at"])
    op.create_index(f"ix_{NARRATIVES}_llm_call_id", NARRATIVES, ["llm_call_id"])
    for statement in ddl.append_only_triggers(NARRATIVES):
        op.execute(statement)
    _from_call(NARRATIVES, "brand")

    op.create_table(
        ASSIGNMENTS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("narrative_id", sa.UUID(), nullable=False),
        sa.Column("mention_id", sa.UUID(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("llm_call_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            GROUPING_PROMPT, name=op.f(f"ck_{ASSIGNMENTS}_prompt_from_grouping_task")
        ),
        _fk(ASSIGNMENTS, "narrative_id", NARRATIVES),
        _fk(ASSIGNMENTS, "mention_id", "mentions"),
        _fk(ASSIGNMENTS, "llm_call_id", "llm_calls"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ASSIGNMENTS}")),
        sa.UniqueConstraint(
            "mention_id", "created_at", name=op.f(f"uq_{ASSIGNMENTS}_mention_id_created_at")
        ),
    )
    op.create_index(f"ix_{ASSIGNMENTS}_narrative_id", ASSIGNMENTS, ["narrative_id"])
    op.create_index(f"ix_{ASSIGNMENTS}_llm_call_id", ASSIGNMENTS, ["llm_call_id"])
    for statement in ddl.append_only_triggers(ASSIGNMENTS):
        op.execute(statement)
    op.execute(SAME_BRAND_FUNCTION)
    op.execute(
        f"CREATE TRIGGER trg_{ASSIGNMENTS}_same_brand BEFORE INSERT ON {ASSIGNMENTS} "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_narrative_assignment_same_brand()"
    )
    _from_call(ASSIGNMENTS, "mention")


def downgrade() -> None:
    op.execute(f"DROP TRIGGER trg_{ASSIGNMENTS}_from_call ON {ASSIGNMENTS}")
    op.execute(f"DROP TRIGGER trg_{ASSIGNMENTS}_same_brand ON {ASSIGNMENTS}")
    op.execute("DROP FUNCTION serpsense_narrative_assignment_same_brand()")
    for statement in ddl.drop_append_only_triggers(ASSIGNMENTS):
        op.execute(statement)
    op.drop_table(ASSIGNMENTS)
    op.execute(f"DROP TRIGGER trg_{NARRATIVES}_from_call ON {NARRATIVES}")
    for statement in ddl.drop_append_only_triggers(NARRATIVES):
        op.execute(statement)
    op.drop_table(NARRATIVES)
