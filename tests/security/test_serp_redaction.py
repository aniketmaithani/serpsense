"""SerpApi payload redaction: the key and author identity never survive (ADR-0007, AGENTS §6).

Payloads are synthetic, shaped like the responses recorded for the demo brand.
"""

import copy
import json
from typing import Any

import pytest

from serpsense.adapters.serp.redaction import redact
from serpsense.domain.search import contains_api_key

pytestmark = pytest.mark.security

KEY = "f" * 64
NEXT = f"https://serpapi.com/search.json?engine=google&q=ola&start=10&api_key={KEY}"


def payload() -> dict[str, Any]:
    return {
        "search_metadata": {
            "id": "abc123",
            "status": "Success",
            "created_at": "2026-10-03 03:42:45 UTC",
            "json_endpoint": "https://serpapi.com/searches/abc123/1.json",
            "raw_html_file": "https://serpapi.com/searches/abc123/1.html",
            "google_url": "https://www.google.co.in/search?q=Ola",
        },
        "search_parameters": {"engine": "google", "q": "Ola", "api_key": KEY},
        "serpapi_pagination": {"current": 1, "next": NEXT, "other_pages": {"2": NEXT}},
        "organic_results": [{"position": 1, "title": "Ola", "link": "https://www.olacabs.com/"}],
        "news_results": [
            {"title": "Ola fares", "source": {"name": "Example Times", "authors": ["A Writer"]}}
        ],
        "reviews": [
            {
                "id": "r1",
                "title": "Reviewer Name",
                "avatar": "https://play-lh.example/avatar.png",
                "rating": 1.0,
                "snippet": "Driver cancelled twice",
                "response": {"title": "Ola", "snippet": "Sorry to hear that"},
            }
        ],
        "maps": {
            "reviews": [{"user": {"name": "Local Guide"}, "link": "https://g.co/r", "rating": 2}]
        },
        "video_results": [
            {"title": "Ola review", "channel": {"name": "Creator"}, "video_id": "v1"}
        ],
        "channel_results": [{"title": "Creator", "handle": "@creator"}],
        "developer_contact": {"support_email": "support@example.com"},
        "place_results": {
            "user_reviews": {
                "most_relevant": [
                    {
                        "username": "Local Guide",
                        "contributor_id": "123",
                        "link": "https://www.google.com/maps/contrib/123",
                        "rating": 4,
                        "description": "Quick service",
                    }
                ]
            }
        },
        "organic_results_with_author": [{"title": "Blog post", "author": "A Writer"}],
        "notes": [f"token {KEY}", "kept"],
        f"odd-{KEY}": "dropped with its key",
    }


def test_the_key_never_survives() -> None:
    result = redact(payload(), api_key=KEY)
    text = json.dumps(result)
    assert KEY not in text
    assert not contains_api_key(result)
    assert result["search_parameters"] == {"engine": "google", "q": "Ola"}
    assert result["serpapi_pagination"] == {"current": 1, "other_pages": {}}
    assert result["notes"] == ["kept"]


@pytest.mark.parametrize(
    "leak",
    [
        "https://serpapi.com/search?q=ola&api_key=" + "0" * 64,  # another (rotated) key
        "\x1api_key=x",  # reads as api_key= once Postgres writes chr(26) as \u001a
    ],
)
def test_any_api_key_string_is_removed(leak: str) -> None:
    result = redact({"a": leak, "b": [leak, "kept"], "c": {"api_key": "not the key"}}, api_key=KEY)
    assert result == {"b": ["kept"], "c": {}}


def test_search_metadata_keeps_no_urls() -> None:
    source = payload()
    source["search_metadata"]["endpoints"] = {"pixel": "https://serpapi.com/x.png", "n": 1}
    source["search_metadata"]["mirrors"] = ["http://serpapi.com/x.html", "kept"]
    metadata = redact(source, api_key=KEY)["search_metadata"]
    assert metadata == {
        "id": "abc123",
        "status": "Success",
        "created_at": "2026-10-03 03:42:45 UTC",
        "endpoints": {"n": 1},
        "mirrors": ["kept"],
    }
    assert "search_metadata" not in redact({"search_metadata": "https://x"}, api_key=KEY)


def test_author_identity_is_removed_and_content_kept() -> None:
    result = redact(payload(), api_key=KEY)
    assert result["news_results"] == [{"title": "Ola fares", "source": {"name": "Example Times"}}]
    assert result["reviews"] == [
        {
            "id": "r1",
            "rating": 1.0,
            "snippet": "Driver cancelled twice",
            "response": {"title": "Ola", "snippet": "Sorry to hear that"},  # the developer's reply
        }
    ]
    assert result["maps"] == {"reviews": [{"rating": 2}]}
    assert result["video_results"] == [{"title": "Ola review", "video_id": "v1"}]
    assert "channel_results" not in result and "developer_contact" not in result
    reviews = result["place_results"]["user_reviews"]["most_relevant"]
    assert reviews == [{"rating": 4, "description": "Quick service"}]
    assert result["organic_results_with_author"] == [{"title": "Blog post"}]
    assert result["organic_results"][0]["title"] == "Ola"  # titles outside reviews stay


def test_input_is_untouched_and_redaction_is_idempotent() -> None:
    original = payload()
    snapshot = copy.deepcopy(original)
    once = redact(original, api_key=KEY)
    assert original == snapshot
    assert redact(once, api_key=KEY) == once


def test_tuples_are_redacted_like_lists() -> None:
    result = redact(
        {"reviews": ({"title": "Reviewer", "rating": 1},), "notes": (f"x {KEY}", "kept")},
        api_key=KEY,
    )
    assert result == {"reviews": [{"rating": 1}], "notes": ["kept"]}


def test_an_empty_key_is_refused() -> None:
    with pytest.raises(ValueError):
        redact(payload(), api_key="")
