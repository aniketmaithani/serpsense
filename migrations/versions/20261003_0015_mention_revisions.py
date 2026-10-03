"""Edited reviews keep their history: mention revisions (owner decision, 2026-10-03).

A mention's own text is revision 1; each text a scan sees that differs from the latest adds the
next revision. Only reviews have revisions; the scan must be of the mention's brand, and only the
text may change later (a redaction scrub), through the generic trigger functions of migration
0011. Rows are never deleted.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from serpsense.adapters.db.ddl import FORBID_MUTATION_FUNCTION

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "mention_revisions"
IMMUTABLE = ("mention_id", "revision", "scan_id", "created_at")
REVIEW_ONLY_FUNCTION = """
CREATE FUNCTION serpsense_mention_revision_is_review() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- A missing mention gives NULL, so its foreign key reports it.
    IF (SELECT source FROM mentions WHERE id = NEW.mention_id)
       NOT IN ('play_review', 'maps_review') THEN
        RAISE EXCEPTION 'only reviews have revisions'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_mention_revisions_review_only';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("mention_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.SmallInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("scan_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 2", name=op.f(f"ck_{TABLE}_revision_after_first")),
        sa.CheckConstraint(
            "text ~ '\\S' AND char_length(text) <= 10000", name=op.f(f"ck_{TABLE}_text_length")
        ),
        sa.ForeignKeyConstraint(
            ["mention_id"],
            ["mentions.id"],
            name=op.f(f"fk_{TABLE}_mention_id_mentions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f(f"fk_{TABLE}_scan_id_scans"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("mention_id", "revision", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("mention_id", "scan_id", name=op.f(f"uq_{TABLE}_mention_id_scan_id")),
    )
    op.create_index(f"ix_{TABLE}_scan_id", TABLE, ["scan_id"])
    op.execute(REVIEW_ONLY_FUNCTION)
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_review_only BEFORE INSERT ON {TABLE} FOR EACH ROW "
        "EXECUTE FUNCTION serpsense_mention_revision_is_review()"
    )
    # History: never deleted (UPDATE stays open for a redaction scrub of the text).
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_no_delete BEFORE DELETE ON {TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {FORBID_MUTATION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_no_truncate BEFORE TRUNCATE ON {TABLE} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {FORBID_MUTATION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_same_brand BEFORE INSERT ON {TABLE} FOR EACH ROW "
        f"EXECUTE FUNCTION serpsense_same_brand_as_scan('mentions', 'mention_id', "
        f"'ck_{TABLE}_same_brand')"
    )
    args = ", ".join(f"'{name}'" for name in (f"ck_{TABLE}_identity_immutable", *IMMUTABLE))
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_identity_immutable BEFORE UPDATE ON {TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION serpsense_forbid_identity_change({args})"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER trg_{TABLE}_identity_immutable ON {TABLE}")
    op.execute(f"DROP TRIGGER trg_{TABLE}_same_brand ON {TABLE}")
    op.execute(f"DROP TRIGGER trg_{TABLE}_no_truncate ON {TABLE}")
    op.execute(f"DROP TRIGGER trg_{TABLE}_no_delete ON {TABLE}")
    op.execute(f"DROP TRIGGER trg_{TABLE}_review_only ON {TABLE}")
    op.execute("DROP FUNCTION serpsense_mention_revision_is_review()")
    op.drop_index(f"ix_{TABLE}_scan_id", table_name=TABLE)
    op.drop_table(TABLE)
