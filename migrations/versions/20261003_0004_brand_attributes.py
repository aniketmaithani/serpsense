"""Brand attributes: aliases, languages, watch terms, apps and locations.

Aliases, watch terms and location queries are citext, so "unique per brand ignoring case" is a
plain unique constraint.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("brand_aliases", "brand_languages", "brand_watch_terms", "brand_apps", "brand_locations")


def _brand_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["brand_id"], ["brands.id"], name=op.f(f"fk_{table}_brand_id_brands"), ondelete="RESTRICT"
    )


def _check(table: str, name: str, sql: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sql, name=op.f(f"ck_{table}_{name}"))


def _id_table(table: str, *items: Any, unique: tuple[str, ...]) -> None:
    """Tables with a surrogate id, a brand FK and one per-brand uniqueness rule."""
    op.create_table(
        table,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        *items,
        _brand_fk(table),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        sa.UniqueConstraint(*unique, name=op.f(f"uq_{table}_{'_'.join(unique)}")),
    )


def upgrade() -> None:
    _id_table(
        "brand_aliases",
        sa.Column("alias", postgresql.CITEXT(), nullable=False),
        _check("brand_aliases", "alias_length", "alias ~ '\\S' AND char_length(alias) <= 120"),
        unique=("brand_id", "alias"),
    )
    op.create_table(
        "brand_languages",
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("language_code", sa.Text(), nullable=False),
        _check(
            "brand_languages",
            "language_code_format",
            "language_code ~ '^[a-z]{2,3}(-[a-z0-9]{2,8})*$'",
        ),
        _brand_fk("brand_languages"),
        sa.PrimaryKeyConstraint("brand_id", "language_code", name=op.f("pk_brand_languages")),
    )
    _id_table(
        "brand_watch_terms",
        sa.Column("term", postgresql.CITEXT(), nullable=False),
        _check("brand_watch_terms", "term_length", "term ~ '\\S' AND char_length(term) <= 80"),
        unique=("brand_id", "term"),
    )
    _id_table(
        "brand_apps",
        sa.Column("store", sa.Enum("google_play", name="app_store"), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        _check("brand_apps", "app_id_format", "app_id ~ '^\\S{1,255}$'"),
        unique=("brand_id", "store", "app_id"),
    )
    _id_table(
        "brand_locations",
        sa.Column("query", postgresql.CITEXT(), nullable=False),
        sa.Column("resolved_data_id", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        _check("brand_locations", "query_length", "query ~ '\\S' AND char_length(query) <= 200"),
        _check(
            "brand_locations",
            "resolution_together",
            "(resolved_data_id IS NULL) = (resolved_at IS NULL)",
        ),
        unique=("brand_id", "query"),
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP TYPE app_store")
