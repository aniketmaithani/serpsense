"""LLM call ledger.

llm_calls is append-only: one row per model call with ids, token counts, cost in integer micros
and the resolved settings, never the prompt or the completion (ADR-0008). A scan's calls belong
to its brand's owner (trg_llm_calls_user_owns_scan, reported as ck_llm_calls_user_owns_scan),
through a generic function that takes the constraint to report, so later ledgers can reuse it.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db import ddl

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CALLS = "llm_calls"
TASKS = (
    "label_mentions",
    "classify_autocomplete",
    "assess_ai_overview",
    "group_narratives",
    "explain_crisis",
    "draft_response",
)
OUTCOMES = ("succeeded", "refused", "truncated", "invalid_output", "failed")
TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
MODEL = "'^[a-z0-9][a-z0-9.-]{0,63}$'"
CHECKS = {
    "served_model_iff_response": "(outcome = 'failed') = (served_model IS NULL)",
    "requested_model_format": f"requested_model ~ {MODEL}",
    "served_model_format": f"served_model ~ {MODEL}",
    "prompt_version_format": "prompt_version ~ '^[a-z][a-z_]{0,47}/v[1-9][0-9]{0,3}$'",
    "prompt_matches_task": "split_part(prompt_version, '/', 1) = task::text",
    "stop_reason_format": "stop_reason ~ '^[a-z][a-z_]{0,31}$'",
    "request_settings_is_object": "jsonb_typeof(request_settings) = 'object'",
    **{f"{column}_non_negative": f"{column} >= 0" for column in TOKENS},
    "cost_micros_non_negative": "cost_micros >= 0",
    "currency_iso_4217": "currency ~ '^[A-Z]{3}$'",
    "latency_non_negative": "latency_ms >= 0",
}

USER_OWNS_SCAN_FUNCTION = """
CREATE FUNCTION serpsense_user_owns_scan() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    brand_owner uuid;
BEGIN
    SELECT b.owner_id INTO brand_owner
    FROM scans s JOIN brands b ON b.id = s.brand_id
    WHERE s.id = NEW.scan_id;
    -- "<>": NULL for a call outside a scan or a missing scan (the foreign key reports it).
    IF brand_owner <> NEW.user_id THEN
        RAISE EXCEPTION 'a scan''s calls belong to its brand''s owner'
            USING ERRCODE = 'check_violation', CONSTRAINT = TG_ARGV[0];
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        CALLS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("scan_id", sa.UUID(), nullable=True),
        sa.Column("task", sa.Enum(*TASKS, name="llm_task"), nullable=False),
        sa.Column("requested_model", sa.Text(), nullable=False),
        sa.Column("served_model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("request_settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *[sa.Column(column, sa.Integer(), nullable=False) for column in TOKENS],
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Enum(*OUTCOMES, name="llm_call_outcome"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *[sa.CheckConstraint(sql, name=op.f(f"ck_{CALLS}_{n}")) for n, sql in CHECKS.items()],
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f(f"fk_{CALLS}_scan_id_scans"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f(f"fk_{CALLS}_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CALLS}")),
    )
    op.create_index(f"ix_{CALLS}_user_id_created_at", CALLS, ["user_id", "created_at"])
    op.create_index(f"ix_{CALLS}_scan_id", CALLS, ["scan_id"])
    for statement in ddl.append_only_triggers(CALLS):
        op.execute(statement)
    op.execute(USER_OWNS_SCAN_FUNCTION)
    op.execute(
        f"CREATE TRIGGER trg_{CALLS}_user_owns_scan BEFORE INSERT ON {CALLS} FOR EACH ROW "
        f"EXECUTE FUNCTION serpsense_user_owns_scan('ck_{CALLS}_user_owns_scan')"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER trg_{CALLS}_user_owns_scan ON {CALLS}")
    op.execute("DROP FUNCTION serpsense_user_owns_scan()")
    for statement in ddl.drop_append_only_triggers(CALLS):
        op.execute(statement)
    op.drop_table(CALLS)
    for enum_type in ("llm_call_outcome", "llm_task"):
        op.execute(f"DROP TYPE {enum_type}")
