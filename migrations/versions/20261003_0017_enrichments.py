"""Enrichments: the model's labels for each text of a mention.

Revision 1 is mentions.text and later revisions are mention_revisions rows, so an edited review
is labelled again; one label per (mention, revision, prompt version), from a labelling task's
prompt. The revision must exist (trg_enrichments_revision_exists → ck_enrichments_revision_exists).
Provenance and immutability guards follow in migration 0018.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "enrichments"
TOPICS = (
    "product_quality",
    "pricing",
    "billing_refunds",
    "customer_service",
    "reliability",
    "safety",
    "app_experience",
    "privacy_security",
    "fraud_scam",
    "marketing_ethics",
    "legal_regulatory",
    "corporate",
    "workforce",
    "other",
)
LABELLING_TASKS = "('label_mentions', 'classify_autocomplete', 'assess_ai_overview')"
CHECKS = {
    "revision_positive": "revision >= 1",
    "prompt_from_labelling_task": f"split_part(prompt_version, '/', 1) IN {LABELLING_TASKS}",
    "sentiment_range": "sentiment BETWEEN -1 AND 1",
    "severity_range": "severity BETWEEN 0 AND 100",
    "reason_length": "reason ~ '\\S' AND char_length(reason) <= 500",
}
REVISION_EXISTS_FUNCTION = """
CREATE FUNCTION serpsense_enrichment_revision_exists() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.revision > 1 AND NOT EXISTS (
        SELECT 1 FROM mention_revisions
        WHERE mention_id = NEW.mention_id AND revision = NEW.revision
    ) THEN
        RAISE EXCEPTION 'an enrichment labels a text the mention has'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_enrichments_revision_exists';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mention_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.SmallInteger(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("llm_call_id", sa.UUID(), nullable=False),
        sa.Column("sentiment", sa.SmallInteger(), nullable=False),
        sa.Column("severity", sa.SmallInteger(), nullable=False),
        sa.Column("topic", sa.Enum(*TOPICS, name="topic"), nullable=False),
        sa.Column("is_complaint", sa.Boolean(), nullable=False),
        sa.Column("is_about_brand", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *[sa.CheckConstraint(sql, name=op.f(f"ck_{TABLE}_{n}")) for n, sql in CHECKS.items()],
        sa.ForeignKeyConstraint(
            ["mention_id"],
            ["mentions.id"],
            name=op.f(f"fk_{TABLE}_mention_id_mentions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["llm_call_id"],
            ["llm_calls.id"],
            name=op.f(f"fk_{TABLE}_llm_call_id_llm_calls"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint(
            "mention_id",
            "revision",
            "prompt_version",
            name=op.f(f"uq_{TABLE}_mention_id_revision_prompt_version"),
        ),
    )
    op.create_index(f"ix_{TABLE}_llm_call_id", TABLE, ["llm_call_id"])
    op.execute(REVISION_EXISTS_FUNCTION)
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_revision_exists BEFORE INSERT ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_enrichment_revision_exists()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER trg_{TABLE}_revision_exists ON {TABLE}")
    op.execute("DROP FUNCTION serpsense_enrichment_revision_exists()")
    op.drop_table(TABLE)
    op.execute("DROP TYPE topic")
