"""Mention rules enforced by Postgres itself (data-model.md §5)."""

from typing import Any

import pytest
from sqlalchemy import Connection, delete, select, text
from sqlalchemy.exc import IntegrityError

from serpsense.domain.enums import MentionSource
from tests.integration.db_helpers import (
    NOW,
    add_app,
    add_location,
    add_mention,
    add_owned_brand,
    table,
    violation,
)

pytestmark = pytest.mark.integration

MENTIONS = table("mentions")
REVIEW: dict[str, Any] = {"identity_key": "r1", "url": None, "outlet": None}


def test_mention_round_trips(conn: Connection) -> None:
    mention_id = add_mention(conn, add_owned_brand(conn))
    row = conn.execute(select(MENTIONS).where(MENTIONS.c.id == mention_id)).one()
    assert (row.source, row.outlet, row.language_code, row.published_at) == (
        "news",
        "Example News",
        "en",
        NOW,
    )


def test_mention_is_unique_per_brand_source_and_key(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    add_mention(conn, brand_id)
    add_mention(conn, brand_id, source="top_story")  # same key, other source
    add_mention(conn, add_owned_brand(conn, "soundnest"))  # same key, other brand
    with pytest.raises(IntegrityError) as exc:
        add_mention(conn, brand_id)
    assert violation(exc).constraint_name == "uq_mentions_brand_id_source_identity_key"


# (review source, citing column, factory, cited table)
CITATIONS = [
    ("maps_review", "brand_location_id", add_location, "brand_locations"),
    ("play_review", "brand_app_id", add_app, "brand_apps"),
]


@pytest.mark.parametrize(("source", "column", "factory", "target"), CITATIONS)
def test_reviews_cite_their_own_brand_only(
    conn: Connection, source: str, column: str, factory: Any, target: str
) -> None:
    brand_id = add_owned_brand(conn)
    add_mention(conn, brand_id, source=source, **REVIEW, **{column: factory(conn, brand_id)})
    other = factory(conn, add_owned_brand(conn, "soundnest"))
    with pytest.raises(IntegrityError) as exc:
        add_mention(
            conn, brand_id, source=source, **{**REVIEW, "identity_key": "r2", column: other}
        )
    assert violation(exc).constraint_name == f"fk_mentions_{column}_{target}"


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"source": "maps_review"}, "location_iff_maps_review"),
        ({"brand_location_id": "own_location"}, "location_iff_maps_review"),
        ({"source": "play_review"}, "app_iff_play_review"),
        ({"brand_app_id": "own_app"}, "app_iff_play_review"),
        ({"identity_key": "https://news.example.in/a"}, "identity_key_hashed"),
        ({"source": "youtube_video", "identity_key": ""}, "identity_key_length"),
        ({"source": "youtube_video", "identity_key": "k" * 513}, "identity_key_length"),
        ({"text": " \n"}, "text_length"),
        ({"text": "x" * 10001}, "text_length"),
        ({"url": "ftp://news.example.in/a"}, "url_format"),
        ({"url": "https://news.example.in/a b"}, "url_format"),
        ({"url": "https://e.in/" + "a" * 2040}, "url_format"),
        ({"outlet": "o" * 201}, "outlet_length"),
        ({"source": "youtube_video"}, "outlet_news_only"),
        ({"source": "play_review", "brand_app_id": "own_app", "outlet": None}, "review_has_no_url"),
        (
            {"source": "maps_review", "brand_location_id": "own_location", "outlet": None},
            "review_has_no_url",
        ),
        ({"language_code": "EN"}, "language_code_format"),
    ],
)
def test_mention_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    brand_id = add_owned_brand(conn)
    if overrides.get("brand_location_id") == "own_location":
        overrides = {**overrides, "brand_location_id": add_location(conn, brand_id)}
    if overrides.get("brand_app_id") == "own_app":
        overrides = {**overrides, "brand_app_id": add_app(conn, brand_id)}
    with pytest.raises(IntegrityError) as exc:
        add_mention(conn, brand_id, **overrides)
    assert violation(exc).constraint_name == f"ck_mentions_{check}"


def test_mention_accepts_limits(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    add_mention(
        conn, brand_id, text="x" * 10000, outlet="o" * 200, url="https://e.in/" + "a" * 2035
    )
    add_mention(conn, brand_id, source="youtube_video", identity_key="k" * 512, outlet=None)


def test_brand_with_mentions_cannot_be_deleted(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    add_mention(conn, brand_id)  # a news mention: nothing else refers to the brand
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table("brands")).where(table("brands").c.id == brand_id))
    assert violation(exc).constraint_name == "fk_mentions_brand_id_brands"


@pytest.mark.parametrize(("source", "column", "factory", "target"), CITATIONS)
def test_cited_location_or_app_cannot_be_deleted(
    conn: Connection, source: str, column: str, factory: Any, target: str
) -> None:
    brand_id = add_owned_brand(conn)
    cited = factory(conn, brand_id)
    add_mention(conn, brand_id, source=source, **REVIEW, **{column: cited})
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table(target)).where(table(target).c.id == cited))
    assert violation(exc).constraint_name == f"fk_mentions_{column}_{target}"


def test_mention_source_enum_matches_domain(conn: Connection) -> None:
    labels = conn.execute(text("SELECT unnest(enum_range(NULL::mention_source))::text")).scalars()
    assert list(labels) == [member.value for member in MentionSource]
