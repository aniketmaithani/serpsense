"""Trends and Play parsers against real, redacted responses for Ola (ADR-0007)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from serpsense.adapters.serp.parsers import (
    parse_play_product,
    parse_trends_related,
    parse_trends_timeseries,
)
from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import text_key
from serpsense.domain.observation import AppRating

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parents[1] / "fixtures" / "serpapi" / "ola"


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def test_trends_points_cover_every_line_and_flag_the_partial_day() -> None:
    points = parse_trends_timeseries(fixture("trends_timeseries"))
    assert len(points) == 6 * 5  # six days, five lines (Ola and its four competitors)
    first = points[0]
    assert (first.query, first.observed_at, first.interest) == (
        "Ola",
        datetime(2026, 7, 3, tzinfo=UTC),
        77,
    )
    sent = ["Ola", "Uber", "Rapido", "Namma Yatri", "inDrive"]  # q, in the order it was sent
    assert {(p.query_index, p.query) for p in points} == set(enumerate(sent))
    assert [p.is_partial for p in points if p.query == "Ola"] == [False] * 5 + [True]
    assert min(p.interest for p in points) == 0  # "<1" arrives as 0


def test_related_queries_rank_rising_before_top() -> None:
    mentions = parse_trends_related(fixture("trends_related"))
    assert len(mentions) == 10 and all(m.source is MentionSource.TRENDS_QUERY for m in mentions)
    assert (mentions[0].position, mentions[0].text) == (1, "ola electric shakti portfolio")
    assert mentions[0].identity_key == text_key("ola electric shakti portfolio")
    assert [m.position for m in mentions] == [1, 2, 3, 4, 5, 26, 27, 28, 29, 30]


def test_a_top_querys_rank_never_depends_on_the_rising_list() -> None:
    top = [{"query": "ola share price"}]
    for rising in ([], [{"query": f"ola {n}"} for n in range(30)]):
        payload = {"related_queries": {"rising": rising, "top": top}}
        ranks = {m.text: m.position for m in parse_trends_related(payload)}
        assert ranks["ola share price"] == 26
        assert len(ranks) == min(len(rising), 25) + 1  # at most 25 rising queries


def test_play_page_gives_the_rating_and_reviews() -> None:
    rating, reviews = parse_play_product(fixture("play_product"))
    assert rating == AppRating(rating_hundredths=460, review_count=3_540_000)
    assert len(reviews) == 5 and [m.position for m in reviews] == [1, 2, 3, 4, 5]
    first = reviews[0]
    assert (first.source, first.star_rating, first.url, first.outlet) == (
        MentionSource.PLAY_REVIEW,
        1,
        None,
        None,
    )
    assert first.published_at == datetime(2025, 5, 9, 8, 1, 32, tzinfo=UTC)
    assert all(m.identity_key and len(m.identity_key) <= 512 for m in reviews)


@pytest.mark.parametrize(
    ("info", "rating"),
    [
        (
            {"rating": 4.005, "reviews": 10},
            AppRating(rating_hundredths=401, review_count=10),
        ),  # half up
        ({"rating": 0, "reviews": 0}, None),  # no rating yet: no row
        ({"rating": "4.6", "reviews": 10}, None),
        ({"reviews": 10}, None),
        (None, None),
    ],
)
def test_app_rating_edge_cases(info: object, rating: AppRating | None) -> None:
    assert parse_play_product({"product_info": info})[0] == rating


def test_malformed_trends_and_reviews_are_skipped() -> None:
    timeline = {
        "interest_over_time": {
            "timeline_data": [
                {
                    "timestamp": "x",
                    "values": [{"query": "Ola", "query_index": 0, "extracted_value": 5}],
                },
                {
                    "timestamp": "1790985600",
                    "values": [
                        {"query": "Ola", "query_index": 0, "extracted_value": 101},
                        {"query": " ", "query_index": 0, "extracted_value": 5},
                        {"query": "Uber", "query_index": 1, "extracted_value": True},
                        {"query": "Uber", "extracted_value": 50},  # no place in the comparison
                        {"query": "Rapido", "query_index": "2", "extracted_value": 40},
                        {"query": "Ola", "query_index": 0, "extracted_value": 9},
                    ],
                },
            ]
        }
    }
    assert [(p.query, p.interest) for p in parse_trends_timeseries(timeline)] == [("Ola", 9)]
    reviews = {
        "reviews": [
            {"id": "r1"},
            {"snippet": "no id"},
            {"id": "r2", "snippet": "Late again", "rating": 9},
        ]
    }
    (only,) = parse_play_product(reviews)[1]
    assert (only.identity_key, only.star_rating) == ("r2", None)
