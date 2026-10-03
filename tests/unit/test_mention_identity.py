"""Mention identity and the rules a parsed mention must meet (data-model §5)."""

from datetime import datetime
from typing import Any

import pytest

from serpsense.domain.enums import MentionSource, Surface
from serpsense.domain.mention import (
    SURFACE,
    ParsedMention,
    best_ranked,
    canonical_url,
    clean_language,
    clean_url,
    normalised_text,
    text_key,
    url_key,
)

pytestmark = pytest.mark.unit

ARTICLE = "https://news.example.in/ola-fares"


@pytest.mark.parametrize(
    "variant",
    [
        "https://News.Example.IN/ola-fares/",
        "HTTPS://news.example.in/ola-fares#comments",
        "https://news.example.in/ola-fares?utm_source=x&utm_medium=y&gclid=z",
        " https://news.example.in/ola-fares?fbclid=x ",
    ],
)
def test_tracking_case_and_fragments_never_make_a_second_mention(variant: str) -> None:
    assert url_key(variant) == url_key(ARTICLE)


def test_meaningful_query_parameters_are_kept_in_order() -> None:
    assert canonical_url("https://x.in/a?b=2&a=1&utm_x=1") == "https://x.in/a?a=1&b=2"
    assert url_key("https://x.in/a?id=1") != url_key("https://x.in/a?id=2")
    assert url_key("https://x.in/a?ref=123") != url_key("https://x.in/a?ref=456")


def test_googles_click_parameters_are_dropped_only_on_google() -> None:
    assert (
        canonical_url("https://www.google.co.in/url?q=x&ved=1&ei=2")
        == "https://www.google.co.in/url?q=x"
    )
    assert canonical_url("https://x.in/a?ei=2") == "https://x.in/a?ei=2"


def test_route_fragments_name_different_pages() -> None:
    assert url_key("https://x.in/#/story/1") != url_key("https://x.in/#/story/2")
    assert url_key("https://x.in/a#!b") != url_key("https://x.in/a")


def test_text_identity_ignores_case_width_and_spacing() -> None:
    assert normalised_text("  Is  OLA　safe? ") == "is ola safe?"
    assert text_key("Is Ola safe?") == text_key("is  ola SAFE?")
    assert len(text_key("ओला")) == 64


@pytest.mark.parametrize(
    ("raw", "url"),
    [
        (ARTICLE, ARTICLE),
        ("ftp://x.in/a", None),
        ("https://x.in/a b", None),
        (ARTICLE + "\n", None),  # Postgres' $ allows no trailing newline
        (None, None),
    ],
)
def test_clean_url(raw: object, url: str | None) -> None:
    assert clean_url(raw) == url
    assert clean_url("https://x.in/" + "a" * 2048) is None


@pytest.mark.parametrize(
    ("raw", "code"),
    [("en", "en"), ("HI", "hi"), ("en-IN", "en-in"), ("e", None), ("en\n", None)],
)
def test_clean_language(raw: str, code: str | None) -> None:
    assert clean_language(raw) == code


def news(**overrides: Any) -> ParsedMention:
    values: dict[str, Any] = {
        "source": MentionSource.NEWS,
        "identity_key": url_key(ARTICLE),
        "text": "Ola revises fares",
        "url": ARTICLE,
        "outlet": "Example News",
        "language_code": "en",
        "position": 1,
    }
    return ParsedMention(**{**values, **overrides})


def test_a_valid_mention_is_accepted() -> None:
    assert news().identity_key == url_key(ARTICLE)
    review = {"url": None, "outlet": None, "identity_key": "gp:AOqpTO", "star_rating": 1}
    assert news(source=MentionSource.PLAY_REVIEW, **review).star_rating == 1
    video = {"url": None, "outlet": None, "identity_key": "a" * 64}  # any provider id
    assert news(source=MentionSource.YOUTUBE_VIDEO, **video).identity_key == "a" * 64


@pytest.mark.parametrize(
    "overrides",
    [
        {"identity_key": ARTICLE},  # a URL source needs a hashed key
        {"source": MentionSource.YOUTUBE_VIDEO, "outlet": None, "identity_key": ""},
        {"text": "Ola\x00"},  # Postgres text can't hold NUL
        {"url": ARTICLE + "\n"},
        {"published_at": datetime(2026, 10, 3)},  # noqa: DTZ001  # a naive time is refused
        {"star_rating": 1},  # stars only on reviews
        {
            "source": MentionSource.PLAY_REVIEW,
            "identity_key": "r1",
            "url": None,
            "outlet": None,
            "star_rating": 6,
        },
        {"text": "Ola \ud800"},  # a lone surrogate can't be stored
        {"position": True},
        {"position": 40_000},  # past a smallint
        {"text": "  "},
        {"text": "x" * 10_001},
        {"url": "ftp://x.in"},
        {
            "source": MentionSource.PLAY_REVIEW,
            "identity_key": "r1",
            "outlet": None,
        },  # url on a review
        {"source": MentionSource.SERP_RESULT},  # outlet only for news
        {"outlet": "o" * 201},
        {"language_code": "EN"},
        {"position": 0},
        {"star_rating": 6},
    ],
)
def test_a_mention_the_database_would_reject_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        news(**overrides)


def test_each_identity_keeps_its_best_rank_whatever_the_order() -> None:
    other = url_key("https://example.in/other")
    seen = [
        news(position=4),
        news(identity_key=other, url=None, position=None),
        news(position=2, text="Ola revises fares again"),
        news(position=None),
        ParsedMention(MentionSource.AUTOCOMPLETE, text_key("ola fares"), "ola fares", position=3),
    ]
    for order in (seen, seen[::-1]):
        assert [(m.source, m.position, m.text) for m in best_ranked(order)] == [
            (MentionSource.AUTOCOMPLETE, 3, "ola fares"),
            (MentionSource.NEWS, 2, "Ola revises fares again"),
            (MentionSource.NEWS, None, "Ola revises fares"),
        ]


def test_every_kind_of_mention_is_seen_on_one_surface() -> None:
    assert dict(SURFACE) == {
        MentionSource.SERP_RESULT: Surface.SEARCH_PAGE,
        MentionSource.TOP_STORY: Surface.SEARCH_PAGE,
        MentionSource.PEOPLE_ALSO_ASK: Surface.SEARCH_PAGE,
        MentionSource.AI_OVERVIEW: Surface.AI_OVERVIEW,
        MentionSource.AUTOCOMPLETE: Surface.AUTOCOMPLETE,
        MentionSource.NEWS: Surface.NEWS,
        MentionSource.TRENDS_QUERY: Surface.TRENDS,
        MentionSource.PLAY_REVIEW: Surface.PLAY,
        MentionSource.MAPS_REVIEW: Surface.MAPS,
        MentionSource.YOUTUBE_VIDEO: Surface.YOUTUBE,
    }
