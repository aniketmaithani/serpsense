"""Parsers against real, redacted SerpApi responses for Ola (ADR-0007), plus the malformed
shapes external data can take."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from serpsense.adapters.serp.parsers import parse_autocomplete, parse_news, parse_search_page
from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import text_key, url_key

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parents[1] / "fixtures" / "serpapi" / "ola"


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def test_search_page_results_and_questions() -> None:
    mentions = parse_search_page(fixture("google"))
    results = [m for m in mentions if m.source is MentionSource.SERP_RESULT]
    questions = [m for m in mentions if m.source is MentionSource.PEOPLE_ALSO_ASK]
    assert [m.position for m in results] == [1, 2, 3, 4, 5]
    first = results[0]
    assert first.url is not None and first.url.startswith("https://apps.apple.com/")
    assert first.identity_key == url_key(first.url)
    assert first.text.startswith("Ola: Book Cab") and "\n" in first.text  # title, then snippet
    assert [m.text for m in questions][:2] == ["How to be Ola free?", "Who is CEO of Ola Cabs?"]
    assert all(m.identity_key == text_key(m.text) and m.url is None for m in questions)
    assert {m.language_code for m in mentions} == {"en"}


def test_autocomplete_keeps_googles_order() -> None:
    mentions = parse_autocomplete(fixture("autocomplete"))
    assert [(m.position, m.text) for m in mentions][:3] == [
        (1, "ola electric"),
        (2, "ola electric scooter"),
        (3, "ola electric share price"),
    ]
    assert len(mentions) == 6 and all(m.source is MentionSource.AUTOCOMPLETE for m in mentions)


def test_news_articles_carry_their_publisher_and_time() -> None:
    mentions = parse_news(fixture("news"))
    assert [m.position for m in mentions] == [1, 2, 3, 4, 5]
    first = mentions[0]
    assert (first.outlet, first.published_at) == (
        "sahi.com",
        datetime(2026, 10, 1, 4, 21, 48, tzinfo=UTC),
    )
    assert all(m.url and m.identity_key == url_key(m.url) for m in mentions)


def test_unusable_items_are_skipped_and_duplicates_keep_their_best_rank() -> None:
    page = {
        "search_parameters": {"hl": "EN"},
        "organic_results": [
            {"position": 3, "title": "Ola fares", "link": "https://x.in/a?utm_source=g"},
            {"position": 1, "title": "Ola fares", "link": "https://x.in/a"},  # the same article
            {"position": 2, "title": "No link"},
            {"position": 4, "link": "https://x.in/no-title"},
            {"position": 5, "title": "Bad link", "link": "javascript:alert(1)"},
            {"position": True, "title": "Odd rank", "link": "https://x.in/odd"},  # unranked
            "not an object",
        ],
        "related_questions": [
            {"question": "  "},
            {"question": "Is Ola safe?"},
            {"question": "Broken \ud800 text"},  # a lone surrogate can't be keyed: skipped
            {"question": "NUL \x00 inside"},  # Postgres text can't hold it: skipped
        ],
    }
    with capture_logs() as logs:
        mentions = parse_search_page(page)
    assert [(m.source, m.position) for m in mentions] == [
        (MentionSource.PEOPLE_ALSO_ASK, 2),
        (MentionSource.SERP_RESULT, 1),
        (MentionSource.SERP_RESULT, None),
    ]
    assert {"event": "serp_parse.items_skipped", "surface": "search_page", "count": 6} in [
        {k: v for k, v in entry.items() if k != "log_level"} for entry in logs
    ]
    assert {m.language_code for m in mentions} == {"en"}


def test_news_times_are_utc_and_outlets_fit_the_column() -> None:
    payload = {
        "news_results": [
            {
                "title": "Ola IPO opens",
                "link": "https://m.in/open",
                "iso_date": "2026-10-02T14:30:00+05:30",
                "source": {"name": "P" * 300},
            },
            {"title": "Ola fares", "link": "https://m.in/fares", "iso_date": "2026-10-02T09:00"},
            {
                "title": "Ancient",
                "link": "https://m.in/old",
                "iso_date": "0001-01-01T00:00:00+05:30",
            },
        ]
    }
    first, second, third = parse_news(payload)
    assert third.published_at is None  # out of range once in UTC
    assert first.published_at == datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
    assert first.published_at.tzinfo is UTC
    assert first.outlet == "P" * 200
    assert second.published_at is None  # a time without a zone isn't trusted


def test_an_organic_result_without_a_position_is_unranked() -> None:
    page = {"organic_results": [{"title": "Ola", "link": "https://x.in"}]}
    assert parse_search_page(page)[0].position is None


@pytest.mark.parametrize("payload", [{}, {"suggestions": "x"}, {"suggestions": [{"value": 3}]}])
def test_missing_or_malformed_lists_give_no_mentions(payload: dict[str, Any]) -> None:
    assert parse_autocomplete(payload) == []


def test_long_text_is_cut_to_the_database_limit() -> None:
    page = {"organic_results": [{"title": "t", "snippet": "s" * 20_000, "link": "https://x.in"}]}
    assert len(parse_search_page(page)[0].text) == 10_000
