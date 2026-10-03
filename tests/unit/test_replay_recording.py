"""Replay recordings: their format, how labels are found, and nothing key-shaped inside."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from serpsense.adapters.replay.recording import (
    RecordedAnswer,
    RecordedLabel,
    RecordedScan,
    Recording,
    cleaned,
    dump,
    load,
    text_id,
)
from serpsense.domain.enums import MentionSource, SerpEngine, Topic
from serpsense.domain.search import params_hash

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 10, 55, tzinfo=UTC)
NEWS = {"q": "Ola", "gl": "in", "hl": "en"}


def recording(payloads: list[dict[str, Any]], *, answer: int = 0) -> Recording:
    scan = RecordedScan(
        recorded_at=AT,
        answers=(RecordedAnswer(engine=SerpEngine.GOOGLE_NEWS, params=NEWS, payload=answer),),
    )
    label = RecordedLabel(
        text_id=text_id(MentionSource.NEWS, "Ola fares rise"),
        prompt_version="label_mentions/v1",
        is_about_brand=True,
        sentiment=-1,
        topic=Topic.PRICING,
        is_complaint=False,
        severity=40,
        reason="Fares went up.",
    )
    return Recording(
        format=1, brand="ola", payloads=tuple(payloads), scans=(scan,), labels=(label,)
    )


def test_a_label_is_found_by_its_source_and_a_short_hash_of_its_text() -> None:
    key = text_id(MentionSource.NEWS, "Ola fares rise")
    assert len(key) == 24 and key == text_id(MentionSource.NEWS, "Ola fares rise")
    assert key != text_id(MentionSource.SERP_RESULT, "Ola fares rise")


def test_an_answer_names_the_request_the_collectors_make() -> None:
    (answer,) = recording([{"news_results": []}]).scans[0].answers
    assert answer.request_hash == params_hash(SerpEngine.GOOGLE_NEWS, NEWS)


def test_cleaning_drops_serpapi_links_pagination_tokens_and_images_at_any_depth() -> None:
    payload = {
        "news_results": [
            {"title": "Ola", "link": "https://news.in/a", "serpapi_link": "x", "thumbnail": "t"}
        ],
        "related": ["https://serpapi.com/search.json?q=ola", "kept"],
        "favicon": "https://cdn.in/f.png",
        "serpapi_pagination": {"next": "https://serpapi.com/next"},
        "reviews": [{"snippet": "Late.", "next_page_token": "abc"}],
        "menu": {"link": "https://serpapi.com/search.json?engine=google_news"},
    }
    assert cleaned(payload) == {
        "news_results": [{"title": "Ola", "link": "https://news.in/a", "serpapi_link": "x"}],
        "related": ["kept"],
        "reviews": [{"snippet": "Late."}],
        "menu": {},
    }


@pytest.mark.parametrize(
    "payload",
    [{"api_key": "x"}, {"note": "?api_key=x"}, {"id": "a" * 64}, {"ids": ["0123456789abcdef" * 4]}],
)
def test_a_recording_with_anything_key_shaped_is_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="shaped like a key"):
        recording([payload])


def test_an_answer_must_point_at_a_kept_payload() -> None:
    with pytest.raises(ValidationError, match="past the recording"):
        recording([{"news_results": []}], answer=1)


def test_a_recording_is_written_on_one_line_and_read_back(tmp_path: Path) -> None:
    original = recording([{"news_results": [{"title": "भारत"}]}])
    text = dump(original)
    assert text.count("\n") == 1 and "भारत" in text  # UTF-8, not escaped
    (tmp_path / "ola.json").write_text(text, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a recording", encoding="utf-8")
    assert load(tmp_path) == (original,)
