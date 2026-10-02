"""Scans.

One scan per brand per schedule slot, and at most one active (queued or running) scan per
brand, enforced by a partial unique index so concurrent dispatchers can't double-run a brand.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_FUNCTION = """
CREATE FUNCTION serpsense_scan_identity_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Only status may change after insert; observations and the Trends comparison rely on
    -- a scan's brand, slot and settings never changing.
    IF (NEW.id, NEW.brand_id, NEW."trigger", NEW.scheduled_for, NEW.requested_by,
        NEW.settings_snapshot, NEW.estimated_searches, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.brand_id, OLD."trigger", OLD.scheduled_for, OLD.requested_by,
        OLD.settings_snapshot, OLD.estimated_searches, OLD.created_at) THEN
        RAISE EXCEPTION 'only a scan''s status can change'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_scans_identity_immutable';
    END IF;
    RETURN NEW;
END;
$$
"""
SCAN_TRIGGER = ("schedule", "manual", "replay")
SCAN_STATUS = ("queued", "running", "succeeded", "partial", "failed", "skipped")


def _check(name: str, sql: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sql, name=op.f(f"ck_scans_{name}"))


def upgrade() -> None:
    op.create_table(
        "scans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("trigger", sa.Enum(*SCAN_TRIGGER, name="scan_trigger"), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("status", sa.Enum(*SCAN_STATUS, name="scan_status"), nullable=False),
        sa.Column("settings_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("estimated_searches", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _check(
            "scheduled_for_schedule", "(\"trigger\" = 'schedule') = (scheduled_for IS NOT NULL)"
        ),
        _check("requested_by_manual", "(\"trigger\" = 'manual') = (requested_by IS NOT NULL)"),
        _check("settings_snapshot_is_object", "jsonb_typeof(settings_snapshot) = 'object'"),
        _check("estimated_searches_non_negative", "estimated_searches >= 0"),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], name=op.f("fk_scans_brand_id_brands"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_scans_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scans")),
        sa.UniqueConstraint(
            "brand_id", "scheduled_for", name=op.f("uq_scans_brand_id_scheduled_for")
        ),
    )
    op.create_index("ix_scans_brand_id_created_at", "scans", ["brand_id", "created_at"])
    op.create_index(
        "uq_scans_brand_id_active",
        "scans",
        ["brand_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_scans_identity_immutable BEFORE UPDATE ON scans "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_scan_identity_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_scans_identity_immutable ON scans")
    op.execute("DROP FUNCTION serpsense_scan_identity_immutable()")
    op.drop_index("uq_scans_brand_id_active", table_name="scans")
    op.drop_index("ix_scans_brand_id_created_at", table_name="scans")
    op.drop_table("scans")
    op.execute("DROP TYPE scan_status")
    op.execute("DROP TYPE scan_trigger")
