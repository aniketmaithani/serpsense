"""The demo story's makings (adapters/replay/story): fictional, consistent, and safe to show."""

from urllib.parse import urlsplit

import pytest

from serpsense.adapters.replay.story import words
from serpsense.adapters.replay.story.content import STORIES, Content, StoryBrand
from serpsense.adapters.replay.story.soundnest import RIVAL
from serpsense.adapters.replay.story.voltbox import MAIN
from serpsense.domain.model_text import has_contact
from serpsense.domain.settings.search import resolve

pytestmark = pytest.mark.unit


def tagged(content: Content) -> list[object]:
    return [*content.results, *content.questions, *content.articles, *content.reviews,
            *content.queries]  # fmt: skip


def check(brand: StoryBrand) -> None:
    """Settings a scan can use; every link fictional; tags a label can hold; stories known."""
    resolve(brand.settings)
    content = brand.content
    for item in tagged(content):
        tag = item.tag  # type: ignore[attr-defined]  # every tagged item has one
        assert tag.sentiment in (-1, 0, 1) and 0 <= tag.severity <= 100 and tag.reason
        assert tag.story is None or tag.story in STORIES
        assert tag.complaint == (tag.sentiment < 0)
    for link in [*(r.link for r in content.results), *(a.link for a in content.articles)]:
        parts = urlsplit(link)
        assert parts.scheme == "https" and parts.netloc.endswith("example.com"), link
    assert content.rating and content.rating[0][0] == 0  # a rating from the first scan


def test_soundnest_is_fictional_and_untouched() -> None:
    check(RIVAL)
    assert all(item.tag.story is None for item in tagged(RIVAL.content))  # type: ignore[attr-defined]  # tagged


def test_what_the_model_says_names_no_one_and_fits_its_alerts() -> None:
    assert all(not has_contact(text) for _, _, text in words.DRAFTS)  # nothing the drafter flags
    assert {story for story, _, _ in words.DRAFTS} == set(STORIES)
    for _, level, story, said in words.EXPLANATIONS:
        assert level in words.LEVELS and (story == "" or story in STORIES) and said


def test_voltbox_builds_to_a_crisis_and_stops_at_its_peak() -> None:
    check(MAIN)
    shown = [item.at for item in [*tagged(MAIN.content), *MAIN.content.suggestions]]  # type: ignore[attr-defined]  # every item has a time
    assert max(shown) == 20  # the story ends on the morning the crisis peaks
    swelling = [item for item in tagged(MAIN.content) if item.tag.story]  # type: ignore[attr-defined]  # tagged
    assert len(swelling) >= 5 and all(item.tag.sentiment < 0 for item in swelling)  # type: ignore[attr-defined]  # tagged
