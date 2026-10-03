"""Reading the grouping prompt's answer: what is stored, what is set aside, and what makes the
whole answer invalid."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from serpsense.domain.enums import MentionSource, Topic
from serpsense.ports.narrative_store import OpenNarrative, UngroupedMention
from serpsense.services.grouping_answer import (
    Grouping,
    mention_record,
    narrative_record,
    placements,
)

pytestmark = pytest.mark.unit

BRAND = uuid.uuid4()
CASH = OpenNarrative(uuid.uuid4(), "Drivers demand cash above the fare", "Riders report it.", 4)


def waiting(count: int) -> dict[str, UngroupedMention]:
    return {
        f"m{n}": UngroupedMention(
            mention_id=uuid.uuid4(),
            source=MentionSource.PLAY_REVIEW,
            language_code="en",
            text=f"AC extra {n}",
            topic=Topic.PRICING,
            severity=30,
            labelled_at=datetime(2026, 10, 3, tzinfo=UTC),
        )
        for n in range(1, count + 1)
    }


def story(
    key: str, label: str = "Refunds pending", summary: str = "Riders wait."
) -> dict[str, str]:
    return {"id": key, "label": label, "summary": summary}


def answer(stories: list[dict[str, str]], placed: list[tuple[str, str | None]]) -> Grouping:
    rows: list[dict[str, Any]] = [{"id": m, "narrative": n} for m, n in placed]
    return Grouping.model_validate({"new_narratives": stories, "placements": rows})


def test_placements_join_open_and_new_stories_and_one_offs_are_kept() -> None:
    mentions = waiting(4)
    stories = [story("new1"), story("new2", "Unused")]
    placed = [("m1", "n1"), ("m2", "new1"), ("m3", "new1"), ("m4", None)]
    got = placements(answer(stories, placed), BRAND, {"n1": CASH}, mentions)
    (refunds,) = got.narratives  # a story nobody joined isn't started
    assert (refunds.brand_id, refunds.label, refunds.summary) == (
        BRAND,
        "Refunds pending",
        "Riders wait.",
    )
    assert [(a.mention_id, a.narrative_id) for a in got.assignments] == [
        (mentions["m1"].mention_id, CASH.narrative_id),
        (mentions["m2"].mention_id, refunds.narrative_id),
        (mentions["m3"].mention_id, refunds.narrative_id),
    ]
    assert (got.one_offs, got.dropped, got.ignored) == (["m4"], 0, 0)


def test_unusable_answers_are_set_aside_and_counted() -> None:
    mentions = waiting(5)
    stories = [
        story("new1"),
        story("new1", "Again"),
        story("n1", "Clash"),
        story("new2", "x" * 121),
    ]
    placed = [
        ("m1", "new1"),
        ("m1", "n1"),  # a second answer for m1: ignored
        ("m2", "n9"),  # no such narrative: m2 keeps waiting
        ("m3", "new2"),  # its story is too long for the table: m3 keeps waiting
        ("m9", "n1"),  # no such mention: ignored
    ]  # m4 and m5 got no placement: they keep waiting
    got = placements(answer(stories, placed), BRAND, {"n1": CASH}, mentions)
    assert [n.label for n in got.narratives] == ["Refunds pending"]
    assert [a.mention_id for a in got.assignments] == [mentions["m1"].mention_id]
    assert (got.one_offs, got.dropped, got.ignored) == ([], 4, 2)


@pytest.mark.parametrize(
    ("label", "summary"),
    [
        ("Fares\x00", "Riders wait."),  # a NUL the database refuses
        ("Fares\nBcc: someone", "Riders wait."),  # a line break into an email
        (
            "Fares " + chr(0x202E) + "raf",
            "Riders wait.",
        ),  # a bidi override that reorders what is read
        ("Fares", "Riders\x07 wait."),
        (" ", "Riders wait."),
    ],
)
def test_control_or_bidi_characters_make_the_answer_invalid(label: str, summary: str) -> None:
    with pytest.raises(ValidationError):
        answer([story("new1", label, summary)], [("m1", "new1")])


def test_a_summary_may_break_lines() -> None:
    (started,) = answer([story("new1", summary="Riders wait.\nSome for weeks.")], []).new_narratives
    assert started.summary == "Riders wait.\nSome for weeks."


def test_records_carry_what_the_prompt_reads() -> None:
    mention = waiting(1)["m1"]
    assert mention_record("m1", mention) == {
        "id": "m1",
        "source": "play_review",
        "topic": "pricing",
        "severity": "30",
        "text": "AC extra 1",
        "language": "en",
    }
    assert "language" not in mention_record("m1", replace(mention, language_code=None))
    assert narrative_record("n1", CASH) == {
        "id": "n1",
        "mentions": "4",
        "label": CASH.label,
        "text": "Riders report it.",
    }
