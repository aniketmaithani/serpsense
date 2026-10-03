"""Response drafts and their citations: append-only model output.

A draft is a response a person may copy (the model never sends anything, ADR-0008). It never
changes once written: asking again makes a new draft. Guards:
- it comes from a successful call, with its prompt version, made for the owner of the
  narrative's brand (serpsense_output_from_call through the narrative, migration 0025)
  → ck_drafts_from_call;
- one draft per call (uq_drafts_llm_call_id);
- a draft cites mentions of its narrative's brand
  (trg_draft_citations_same_brand → ck_draft_citations_same_brand).
Who asked for a draft is its call's user, so it isn't stored again.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db import ddl

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRAFTS, CITATIONS = "drafts", "draft_citations"
KINDS = ("holding_statement", "review_reply", "faq_entry")
PRESETS = ("standard", "high_thinking", "max")
CITATION_SAME_BRAND = """
CREATE FUNCTION serpsense_draft_citation_same_brand() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- "<>": a missing draft or mention is left to its foreign key to report.
    IF (SELECT n.brand_id FROM drafts d JOIN narratives n ON n.id = d.narrative_id
        WHERE d.id = NEW.draft_id) <> (SELECT brand_id FROM mentions WHERE id = NEW.mention_id)
    THEN
        RAISE EXCEPTION 'a draft cites mentions of its own brand'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_draft_citations_same_brand';
    END IF;
    RETURN NEW;
END;
$$
"""


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


def _drafts() -> None:
    op.create_table(
        DRAFTS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("narrative_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Enum(*KINDS, name="draft_kind"), nullable=False),
        sa.Column("preset", sa.Enum(*PRESETS, name="draft_preset"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("llm_call_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _text(DRAFTS, "text", 4000),
        sa.CheckConstraint(
            "reasoning_summary IS NULL OR char_length(reasoning_summary) <= 8000",
            name=op.f(f"ck_{DRAFTS}_reasoning_summary_length"),
        ),
        _task(DRAFTS, "draft_response"),
        _fk(DRAFTS, "narrative_id", "narratives"),
        _fk(DRAFTS, "llm_call_id", "llm_calls"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{DRAFTS}")),
        sa.UniqueConstraint("llm_call_id", name=op.f(f"uq_{DRAFTS}_llm_call_id")),
    )
    op.create_index(f"ix_{DRAFTS}_narrative_id_created_at", DRAFTS, ["narrative_id", "created_at"])
    _from_call(DRAFTS, "narrative")
    op.create_table(
        CITATIONS,
        sa.Column("draft_id", sa.UUID(), nullable=False),
        sa.Column("mention_id", sa.UUID(), nullable=False),
        _fk(CITATIONS, "draft_id", DRAFTS),
        _fk(CITATIONS, "mention_id", "mentions"),
        sa.PrimaryKeyConstraint("draft_id", "mention_id", name=op.f(f"pk_{CITATIONS}")),
    )
    op.create_index(f"ix_{CITATIONS}_mention_id", CITATIONS, ["mention_id"])
    op.execute(CITATION_SAME_BRAND)
    op.execute(
        f"CREATE TRIGGER trg_{CITATIONS}_same_brand BEFORE INSERT ON {CITATIONS} "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_draft_citation_same_brand()"
    )


def upgrade() -> None:
    _drafts()
    for table in (DRAFTS, CITATIONS):
        for statement in ddl.append_only_triggers(table):
            op.execute(statement)


def downgrade() -> None:
    for table in (CITATIONS, DRAFTS):
        for statement in ddl.drop_append_only_triggers(table):
            op.execute(statement)
    op.execute(f"DROP TRIGGER trg_{CITATIONS}_same_brand ON {CITATIONS}")
    op.execute("DROP FUNCTION serpsense_draft_citation_same_brand()")
    op.drop_table(CITATIONS)
    op.execute(f"DROP TRIGGER trg_{DRAFTS}_from_call ON {DRAFTS}")
    op.drop_table(DRAFTS)
    op.execute("DROP TYPE draft_preset")
    op.execute("DROP TYPE draft_kind")
