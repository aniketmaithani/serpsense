"""SerpApi call ledger and raw responses.

serp_calls is an append-only ledger (billing, usage and the circuit breaker read it).
raw_responses stays mutable so a retention job can prune old payloads.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from serpsense.adapters.db import ddl

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CALLS = "serp_calls"
ENGINES = (
    "google",
    "google_ai_overview",
    "google_autocomplete",
    "google_news",
    "google_trends",
    "google_play_product",
    "google_maps",
    "google_maps_reviews",
    "youtube",
)
SERVED_FROM = ("local_cache", "serpapi_cache", "live")
OUTCOMES = ("succeeded", "failed", "skipped_budget", "circuit_open")
CALL_CHECKS = {
    "served_from_iff_succeeded": "(outcome = 'succeeded') = (served_from IS NOT NULL)",
    "error_code_iff_failed": "(outcome = 'failed') = (error_code IS NOT NULL)",
    "error_code_format": "error_code ~ '^[a-z][a-z0-9_.]{0,63}$'",
    "http_status_range": "http_status BETWEEN 100 AND 599",
    "latency_non_negative": "latency_ms >= 0",
    "params_hash_sha256": "params_hash ~ '^[0-9a-f]{64}$'",
    "params_is_object": "jsonb_typeof(params) = 'object'",
    "params_no_api_key": ddl.no_api_key_check("params"),
}


def upgrade() -> None:
    op.create_table(
        CALLS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("scan_id", sa.UUID(), nullable=True),
        sa.Column("engine", sa.Enum(*ENGINES, name="serp_engine"), nullable=False),
        sa.Column("params_hash", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("served_from", sa.Enum(*SERVED_FROM, name="served_from"), nullable=True),
        sa.Column("outcome", sa.Enum(*OUTCOMES, name="serp_call_outcome"), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *[sa.CheckConstraint(sql, name=op.f(f"ck_{CALLS}_{n}")) for n, sql in CALL_CHECKS.items()],
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f(f"fk_{CALLS}_scan_id_scans"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f(f"fk_{CALLS}_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CALLS}")),
    )
    op.create_index(f"ix_{CALLS}_user_id_created_at", CALLS, ["user_id", "created_at"])
    op.create_index(f"ix_{CALLS}_engine_created_at", CALLS, ["engine", "created_at"])
    op.create_index(f"ix_{CALLS}_scan_id", CALLS, ["scan_id"])
    for statement in ddl.append_only_triggers(CALLS):
        op.execute(statement)

    op.create_table(
        "raw_responses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("serp_call_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_raw_responses_payload_is_object")
        ),
        sa.CheckConstraint(
            ddl.no_api_key_check("payload"), name=op.f("ck_raw_responses_payload_no_api_key")
        ),
        sa.ForeignKeyConstraint(
            ["serp_call_id"],
            [f"{CALLS}.id"],
            name=op.f("fk_raw_responses_serp_call_id_serp_calls"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_responses")),
        sa.UniqueConstraint("serp_call_id", name=op.f("uq_raw_responses_serp_call_id")),
    )


def downgrade() -> None:
    op.drop_table("raw_responses")
    for statement in ddl.drop_append_only_triggers(CALLS):
        op.execute(statement)
    op.drop_table(CALLS)
    for enum_type in ("serp_call_outcome", "served_from", "serp_engine"):
        op.execute(f"DROP TYPE {enum_type}")
