"""Mentions found on search surfaces (docs/architecture/data-model.md §5)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import MentionSource


class Mention(Base):
    """One thing said about a brand on a search surface; author identity is never stored."""

    __tablename__ = "mentions"
    __table_args__ = (
        UniqueConstraint("brand_id", "source", "identity_key"),
        # Composite FKs: a Maps/Play mention must cite a location/app of the same brand.
        ForeignKeyConstraint(
            ["brand_id", "brand_location_id"],
            ["brand_locations.brand_id", "brand_locations.id"],
            name="fk_mentions_brand_location_id_brand_locations",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["brand_id", "brand_app_id"],
            ["brand_apps.brand_id", "brand_apps.id"],
            name="fk_mentions_brand_app_id_brand_apps",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(source = 'maps_review') = (brand_location_id IS NOT NULL)",
            name="location_iff_maps_review",
        ),
        CheckConstraint(
            "(source = 'play_review') = (brand_app_id IS NOT NULL)", name="app_iff_play_review"
        ),
        CheckConstraint("char_length(identity_key) BETWEEN 1 AND 512", name="identity_key_length"),
        # Only reviews and videos keep the provider's id; every other source stores a sha256 hex.
        CheckConstraint(
            "source IN ('play_review', 'maps_review', 'youtube_video') "
            "OR identity_key ~ '^[0-9a-f]{64}$'",
            name="identity_key_hashed",
        ),
        CheckConstraint("text ~ '\\S' AND char_length(text) <= 10000", name="text_length"),
        CheckConstraint("url ~ '^https?://\\S+$' AND char_length(url) <= 2048", name="url_format"),
        CheckConstraint("char_length(outlet) <= 200", name="outlet_length"),
        # Publishers only: a channel or reviewer name, or a reviewer's link, is author identity.
        CheckConstraint(
            "outlet IS NULL OR source IN ('news', 'top_story')", name="outlet_news_only"
        ),
        CheckConstraint(
            "url IS NULL OR source NOT IN ('play_review', 'maps_review')", name="review_has_no_url"
        ),
        CheckConstraint(
            "language_code ~ '^[a-z]{2,3}(-[a-z0-9]{2,8})*$'", name="language_code_format"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT")
    )
    source: Mapped[MentionSource] = mapped_column(pg_enum(MentionSource, "mention_source"))
    # Provider id for reviews and videos, else sha256 hex of the canonical URL or normalised text.
    identity_key: Mapped[str] = mapped_column(Text)
    # Author names and handles are removed at parse time (ADR-0007).
    text: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    outlet: Mapped[str | None] = mapped_column(Text)
    brand_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    brand_app_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    language_code: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
