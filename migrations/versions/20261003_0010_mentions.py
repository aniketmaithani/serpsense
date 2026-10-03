"""Mentions.

A Maps or Play mention must cite a location or app of the same brand. That is enforced with
composite foreign keys onto new (brand_id, id) unique keys of brand_locations / brand_apps,
which therefore have to exist before the mentions table.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = (
    "serp_result",
    "top_story",
    "people_also_ask",
    "autocomplete",
    "ai_overview",
    "news",
    "trends_query",
    "play_review",
    "maps_review",
    "youtube_video",
)
CHECKS = {
    "location_iff_maps_review": "(source = 'maps_review') = (brand_location_id IS NOT NULL)",
    "app_iff_play_review": "(source = 'play_review') = (brand_app_id IS NOT NULL)",
    "identity_key_length": "char_length(identity_key) BETWEEN 1 AND 512",
    "identity_key_hashed": (
        "source IN ('play_review', 'maps_review', 'youtube_video') "
        "OR identity_key ~ '^[0-9a-f]{64}$'"
    ),
    "text_length": "text ~ '\\S' AND char_length(text) <= 10000",
    "url_format": "url ~ '^https?://\\S+$' AND char_length(url) <= 2048",
    "outlet_length": "char_length(outlet) <= 200",
    "outlet_news_only": "outlet IS NULL OR source IN ('news', 'top_story')",
    "review_has_no_url": "url IS NULL OR source NOT IN ('play_review', 'maps_review')",
    "language_code_format": "language_code ~ '^[a-z]{2,3}(-[a-z0-9]{2,8})*$'",
}
COMPOSITE_TARGETS = ("brand_locations", "brand_apps")


def upgrade() -> None:
    for target in COMPOSITE_TARGETS:
        op.create_unique_constraint(op.f(f"uq_{target}_brand_id_id"), target, ["brand_id", "id"])
    op.create_table(
        "mentions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.Enum(*SOURCES, name="mention_source"), nullable=False),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("outlet", sa.Text(), nullable=True),
        sa.Column("brand_location_id", sa.UUID(), nullable=True),
        sa.Column("brand_app_id", sa.UUID(), nullable=True),
        sa.Column("language_code", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *[sa.CheckConstraint(sql, name=op.f(f"ck_mentions_{n}")) for n, sql in CHECKS.items()],
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["brands.id"],
            name=op.f("fk_mentions_brand_id_brands"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "brand_location_id"],
            ["brand_locations.brand_id", "brand_locations.id"],
            name="fk_mentions_brand_location_id_brand_locations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id", "brand_app_id"],
            ["brand_apps.brand_id", "brand_apps.id"],
            name="fk_mentions_brand_app_id_brand_apps",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mentions")),
        sa.UniqueConstraint(
            "brand_id",
            "source",
            "identity_key",
            name=op.f("uq_mentions_brand_id_source_identity_key"),
        ),
    )


def downgrade() -> None:
    op.drop_table("mentions")
    op.execute("DROP TYPE mention_source")
    for target in reversed(COMPOSITE_TARGETS):
        op.drop_constraint(op.f(f"uq_{target}_brand_id_id"), target, type_="unique")
