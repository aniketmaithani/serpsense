"""Google's AI Overview: read from the search page, or from its one follow-up search."""

from typing import Any

import pytest

from serpsense.adapters.serp.collectors import SearchPageCollector
from serpsense.adapters.serp.parsers import ai_overview_token, parse_ai_overview
from serpsense.domain.enums import MentionSource, SerpEngine, Surface
from tests.contract.test_serp_collectors import target

pytestmark = pytest.mark.unit

INLINE: dict[str, Any] = {
    "search_parameters": {"hl": "en"},
    "ai_overview": {
        "text_blocks": [
            {"type": "paragraph", "snippet": "Ola is an Indian ride-hailing company."},
            {
                "type": "list",
                "list": [{"snippet": "Riders report surge fares."}, {"title": "Safety"}],
            },
        ],
        "references": [{"title": "Ola", "link": "https://www.olacabs.com/", "index": 0}],
    },
}
LINKED: dict[str, Any] = {"ai_overview": {"page_token": "abc123token"}}


def test_an_inline_overview_is_one_mention_of_its_text() -> None:
    (mention,) = parse_ai_overview(INLINE)
    assert mention.source is MentionSource.AI_OVERVIEW and mention.position == 1
    assert mention.text.splitlines() == [
        "Ola is an Indian ride-hailing company.",
        "Riders report surge fares.",
        "Safety",
    ]
    assert ai_overview_token(INLINE) is None
    assert parse_ai_overview({}) == [] and parse_ai_overview(LINKED) == []
    assert ai_overview_token(LINKED) == "abc123token"


def test_the_search_page_owns_the_overview_and_follows_its_link_once() -> None:
    collector = SearchPageCollector()
    assert collector.enabled(target()) == {Surface.SEARCH_PAGE, Surface.AI_OVERVIEW}
    (lead,) = collector.leads(target(languages=("en",)))
    assert lead.also == {Surface.AI_OVERVIEW}

    inline = collector.read(lead, INLINE)
    assert [m.source for m in inline.mentions] == [MentionSource.AI_OVERVIEW]
    assert inline.follow_ups == ()

    (follow_up,) = collector.read(lead, LINKED).follow_ups
    assert follow_up.surface is Surface.AI_OVERVIEW
    assert follow_up.request.engine is SerpEngine.GOOGLE_AI_OVERVIEW
    assert follow_up.request.params == {"page_token": "abc123token"}
    assert follow_up.request.no_cache is lead.request.no_cache
    assert collector.read(follow_up, INLINE).mentions == tuple(parse_ai_overview(INLINE))


def test_an_overview_turned_off_or_an_unusable_token_makes_no_search() -> None:
    collector = SearchPageCollector()
    off = target(search_page={"ai_overview": False})
    assert collector.enabled(off) == {Surface.SEARCH_PAGE}
    (lead,) = collector.leads(off)
    assert lead.also == frozenset() and collector.read(lead, LINKED).follow_ups == ()
    (on,) = collector.leads(target())
    for token in ("x" * 5000, "bad\x00token"):
        assert collector.read(on, {"ai_overview": {"page_token": token}}).follow_ups == ()
