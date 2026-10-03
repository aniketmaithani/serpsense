"""Alerts, in-app notifications and their read marks. All append-only.

An alert is a deterministic rule that fired for a scan; its brand is the scan's, so it isn't
stored again. Guards:
- one alert per (scan, rule, narrative), nulls not distinct, so re-running a scan's alert step
  can't duplicate one; a narrative alert names a narrative of the scan's brand
  (trg_alerts_narrative_of_brand → ck_alerts_narrative_of_brand);
- a notification about an alert goes to the owner of the alert's brand, once
  (trg_notifications_alert_owner → ck_notifications_alert_owner).

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db import ddl

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALERTS, NOTIFICATIONS, READS = "alerts", "notifications", "notification_reads"
RULES = ("level_increase", "new_negative_autocomplete", "narrative_spread")
ALERT_KEY = ("scan_id", "rule", "narrative_id")
TEXT_CHECKS = {
    "title_length": "title ~ '\\S' AND char_length(title) <= 200",
    "body_length": "body ~ '\\S' AND char_length(body) <= 2000",
}

NARRATIVE_OF_BRAND = """
CREATE FUNCTION serpsense_alert_narrative_of_brand() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- "<>": a missing scan or narrative is left to its foreign key to report.
    IF (SELECT brand_id FROM narratives WHERE id = NEW.narrative_id)
        <> (SELECT brand_id FROM scans WHERE id = NEW.scan_id) THEN
        RAISE EXCEPTION 'an alert''s narrative is of the scan''s brand'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_alerts_narrative_of_brand';
    END IF;
    RETURN NEW;
END;
$$
"""
ALERT_OWNER = """
CREATE FUNCTION serpsense_notification_alert_owner() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT b.owner_id FROM alerts a JOIN scans s ON s.id = a.scan_id
        JOIN brands b ON b.id = s.brand_id WHERE a.id = NEW.alert_id) <> NEW.user_id THEN
        RAISE EXCEPTION 'a notification about an alert goes to the brand''s owner'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_notifications_alert_owner';
    END IF;
    RETURN NEW;
END;
$$
"""
GUARDS = {
    "serpsense_alert_narrative_of_brand": NARRATIVE_OF_BRAND,
    "serpsense_notification_alert_owner": ALERT_OWNER,
}


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete="RESTRICT"
    )


def _guard(table: str, name: str, function: str) -> None:
    """Install `function` (serpsense_<…>()) as the BEFORE INSERT trigger trg_<table>_<name>."""
    op.execute(GUARDS[function])
    op.execute(
        f"CREATE TRIGGER trg_{table}_{name} BEFORE INSERT ON {table} FOR EACH ROW "
        f"EXECUTE FUNCTION {function}()"
    )


def _drop_guard(table: str, name: str, function: str) -> None:
    op.execute(f"DROP TRIGGER trg_{table}_{name} ON {table}")
    op.execute(f"DROP FUNCTION {function}()")


def _alerts() -> None:
    op.create_table(
        ALERTS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("narrative_id", sa.UUID(), nullable=True),
        sa.Column("rule", sa.Enum(*RULES, name="alert_rule"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(rule = 'narrative_spread') = (narrative_id IS NOT NULL)",
            name=op.f(f"ck_{ALERTS}_narrative_iff_spread"),
        ),
        _fk(ALERTS, "scan_id", "scans"),
        _fk(ALERTS, "narrative_id", "narratives"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ALERTS}")),
        sa.UniqueConstraint(
            *ALERT_KEY,
            name=op.f(f"uq_{ALERTS}_{'_'.join(ALERT_KEY)}"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(f"ix_{ALERTS}_narrative_id", ALERTS, ["narrative_id"])
    _guard(ALERTS, "narrative_of_brand", "serpsense_alert_narrative_of_brand")


def _notifications() -> None:
    op.create_table(
        NOTIFICATIONS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("alert_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *[
            sa.CheckConstraint(sql, name=op.f(f"ck_{NOTIFICATIONS}_{n}"))
            for n, sql in TEXT_CHECKS.items()
        ],
        _fk(NOTIFICATIONS, "user_id", "users"),
        _fk(NOTIFICATIONS, "alert_id", ALERTS),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{NOTIFICATIONS}")),
        sa.UniqueConstraint(
            "alert_id", "user_id", name=op.f(f"uq_{NOTIFICATIONS}_alert_id_user_id")
        ),
    )
    op.create_index(
        f"ix_{NOTIFICATIONS}_user_id_created_at", NOTIFICATIONS, ["user_id", "created_at"]
    )
    _guard(NOTIFICATIONS, "alert_owner", "serpsense_notification_alert_owner")
    op.create_table(
        READS,
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        _fk(READS, "notification_id", NOTIFICATIONS),
        sa.PrimaryKeyConstraint("notification_id", name=op.f(f"pk_{READS}")),
    )


def upgrade() -> None:
    _alerts()
    _notifications()
    for table in (ALERTS, NOTIFICATIONS, READS):
        for statement in ddl.append_only_triggers(table):
            op.execute(statement)


def downgrade() -> None:
    for table in (READS, NOTIFICATIONS, ALERTS):
        for statement in ddl.drop_append_only_triggers(table):
            op.execute(statement)
    op.drop_table(READS)
    _drop_guard(NOTIFICATIONS, "alert_owner", "serpsense_notification_alert_owner")
    op.drop_table(NOTIFICATIONS)
    _drop_guard(ALERTS, "narrative_of_brand", "serpsense_alert_narrative_of_brand")
    op.drop_table(ALERTS)
    op.execute("DROP TYPE alert_rule")
