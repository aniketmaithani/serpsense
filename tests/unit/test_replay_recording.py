"""Replay recordings: format, how output is found, what a payload keeps, nothing key-shaped."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from serpsense.adapters.replay.recording import (
    RecordedAnswer,
    RecordedLabel,
    RecordedNarrative,
    RecordedScan,
    Recording,
    cleaned,
    dump,
    kept_link,
    load,
    review_id,
    text_id,
)
from serpsense.adapters.serp.parsers import (
    parse_autocomplete,
    parse_news,
    parse_play_product,
    parse_search_page,
    parse_trends_related,
    parse_trends_timeseries,
)
from serpsense.domain.enums import MentionSource, SerpEngine, Topic
from serpsense.domain.search import params_hash

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 10, 55, tzinfo=UTC)
NEWS = {"q": "Ola", "gl": "in", "hl": "en"}
FIXTURES = Path(__file__).parents[1] / "fixtures" / "serpapi" / "ola"
G = SerpEngine


KEY = text_id(MentionSource.NEWS, "Ola fares rise")
LABEL = {"text_id": KEY, "prompt_version": "label_mentions/v1", "is_about_brand": True,
         "sentiment": -1, "topic": Topic.PRICING, "is_complaint": False, "severity": 40,
         "reason": "Fares went up."}  # fmt: skip
STORY = {"text_id": KEY, "prompt_version": "group_narratives/v1", "label": "Fare rises",
         "summary": "Riders say fares went up."}  # fmt: skip


def recording(payloads: list[dict[str, Any]], *, answer: int = 0, **kw: Any) -> Recording:
    scan = RecordedScan(
        recorded_at=kw.pop("at", AT),
        settings=kw.pop("settings", {"languages": ["en"]}),
        answers=(RecordedAnswer(engine=G.GOOGLE_NEWS, params=NEWS, payload=answer),),
    )
    return Recording(
        format=1,
        brand="ola",
        name="Ola",
        payloads=tuple(payloads),
        scans=kw.pop("scans", (scan,)),
        labels=(RecordedLabel.model_validate(LABEL),),
        narratives=(RecordedNarrative.model_validate(STORY),),
    )


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def test_output_is_found_by_source_and_text_and_an_answer_by_its_request() -> None:
    assert len(KEY) == 24 and text_id(MentionSource.SERP_RESULT, "Ola fares rise") != KEY
    (answer,) = recording([{"news_results": []}]).scans[0].answers
    assert answer.request_hash == params_hash(G.GOOGLE_NEWS, NEWS)


@pytest.mark.parametrize(
    ("engine", "name", "parse"),
    [
        (G.GOOGLE, "google", parse_search_page),
        (G.GOOGLE_AUTOCOMPLETE, "autocomplete", parse_autocomplete),
        (G.GOOGLE_NEWS, "news", parse_news),
        (G.GOOGLE_TRENDS, "trends_related", parse_trends_related),
        (G.GOOGLE_TRENDS, "trends_timeseries", parse_trends_timeseries),
    ],
)
def test_a_cleaned_payload_parses_as_the_payload_does(engine: G, name: str, parse: Any) -> None:
    payload = fixture(name)
    kept = cleaned(engine, payload)
    assert parse(kept) == parse(links_kept(payload)) and kept != payload and parse(kept)
    assert cleaned(engine, kept) == kept  # only what the parser reads is left


def links_kept(value: Any) -> Any:  # the payload with only its links trimmed
    if isinstance(value, dict):
        return {k: kept_link(v) if k == "link" else links_kept(v) for k, v in value.items()}
    return [links_kept(item) for item in value] if isinstance(value, list) else value


@pytest.mark.parametrize(
    ("link", "kept"),
    [
        ("https://news.in/a?gclid=x&sxsrf=y&sig=z#:~:text=Ola", "https://news.in/a"),
        ("https://youtu.be/abc?si=share123", "https://youtu.be/abc"),
        ("https://www.youtube.com/watch?v=abc&t=10&utm_source=x", "https://www.youtube.com/watch?v=abc"),
        ("https://play.google.com/store/apps/details?id=com.olacabs&hl=en", "https://play.google.com/store/apps/details?id=com.olacabs"),
        ("https://www.babushahi.com/full-news.php?id=123&fbclid=x", "https://www.babushahi.com/full-news.php?id=123"),
        ("https://app.example.in/#/rides?ref=x", "https://app.example.in/#/rides?ref=x"),
    ],
)  # fmt: skip
def test_a_link_keeps_only_the_parameters_that_address_its_content(link: str, kept: str) -> None:
    assert kept_link(link) == kept and kept_link(kept) == kept


def test_play_reviews_keep_their_identity_under_a_hash_and_nothing_else() -> None:
    payload = {**fixture("play_product"), "reviews": [{**fixture("play_product")["reviews"][0]}]}
    payload["reviews"][0] |= {"response": {"snippet": "Hi Priya"}, "title": "Priya K"}
    kept = cleaned(G.GOOGLE_PLAY_PRODUCT, payload)
    (review,) = kept["reviews"]
    assert set(review) <= {"id", "snippet", "rating", "iso_date"}
    assert review["id"] == review_id(payload["reviews"][0]["id"]) != payload["reviews"][0]["id"]
    (rating, (mention,)), (before, (original,)) = map(parse_play_product, (kept, payload))
    assert rating == before and mention == replace(original, identity_key=review["id"])
    assert cleaned(G.GOOGLE_PLAY_PRODUCT, kept) == kept  # a kept id is left alone


def test_cleaning_drops_links_images_tokens_and_unread_blocks_at_any_depth() -> None:
    article = {
        "title": "Ola fares rise",
        "link": "https://news.in/ola?gclid=x&sxsrf=y&sig=z",
        "thumbnail": "https://img.in/" + "a" * 64,
        "stories": [{"title": "unread"}],
        "source": {"name": "News", "icon": "https://img.in/i.png", "authors": ["A"]},
    }
    payload = {"search_parameters": {"hl": "en", "q": "Ola"}, "news_results": [article, "text"],
               "menu_links": [{"serpapi_link": "https://serpapi.com/x", "topic_token": "t"}],
               "serpapi_pagination": {"next": "https://serpapi.com/next"}}  # fmt: skip
    kept = {"title": "Ola fares rise", "link": "https://news.in/ola", "source": {"name": "News"}}
    assert cleaned(G.GOOGLE_NEWS, payload) == {
        "search_parameters": {"hl": "en"},
        "news_results": [kept, "text"],  # the link without its tracking parameters
    }
    assert cleaned(G.YOUTUBE, payload) == {}  # no collector reads it


@pytest.mark.parametrize(
    "payload",
    [{"api_key": "x"}, {"note": "?api_key=x"}, {"id": "a" * 64}, {"ids": ["0123456789ABCDEF" * 4]}],
)
def test_a_recording_with_anything_key_shaped_is_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="shaped like a key"):
        recording([payload])
    with pytest.raises(ValidationError, match="shaped like a key"):
        recording([{}], settings={"templates": [payload]})


def test_answers_point_at_kept_payloads_and_scans_keep_the_order_they_ran() -> None:
    with pytest.raises(ValidationError, match="past the recording"):
        recording([{"news_results": []}], answer=1)
    first = recording([{}]).scans[0]
    later = first.model_copy(update={"recorded_at": AT + timedelta(hours=12)})
    assert len(recording([{}], scans=(first, later)).scans) == 2
    for scans in ((later, first), (first, first)):
        with pytest.raises(ValidationError, match="order they ran"):
            recording([{}], scans=scans)


def test_a_recording_is_written_on_one_line_and_read_back(tmp_path: Path) -> None:
    original = recording([{"news_results": [{"title": "भारत"}]}])
    text = dump(original)
    assert text.count("\n") == 1 and "भारत" in text  # UTF-8, not escaped
    (tmp_path / "ola.json").write_text(text, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a recording", encoding="utf-8")
    assert load(tmp_path) == (original,) and original.narratives[0].label == "Fare rises"
