"""Replay mode's grouping: each text joins the story it was recorded in, or starts it, and every
model task a scan calls has a replayed answer."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from serpsense.adapters.llm.prompts import PROMPTS as PROMPT_FILES
from serpsense.adapters.llm.replay import REPLAY_MODEL, ReplayLlm
from serpsense.adapters.replay.recording import (
    RecordedLabel,
    RecordedNarrative,
    RecordedScan,
    Recording,
    text_id,
)
from serpsense.domain.enums import LlmTask, MentionSource, Topic
from serpsense.domain.llm_capabilities import OPUS, Effort, TaskSettings, request_shape
from serpsense.ports.llm_client import LlmRequest

pytestmark = pytest.mark.unit

GROUPING = "group_narratives/v1"
LATE, AGAIN, REFUND, ONE_OFF = (
    "Driver cancelled twice",
    "Driver cancelled again at the airport",
    "Refund still not credited",
    "App crashed once",
)
# Every task a scan may call: the labellers (domain/labelling.py) and grouping. Once a task has a
# prompt file, a scan calls it, so replay must answer it.
SCAN_TASKS = {
    LlmTask.LABEL_MENTIONS,
    LlmTask.CLASSIFY_AUTOCOMPLETE,
    LlmTask.ASSESS_AI_OVERVIEW,
    LlmTask.GROUP_NARRATIVES,
}


def story(text: str, label: str, prompt: str = GROUPING) -> RecordedNarrative:
    key = text_id(MentionSource.PLAY_REVIEW, text)
    return RecordedNarrative(
        text_id=key, prompt_version=prompt, label=label, summary=f"{label}: what riders say."
    )


def recording(*stories: RecordedNarrative, name: str = "Ola") -> Recording:
    label = RecordedLabel(
        text_id=text_id(MentionSource.PLAY_REVIEW, LATE),
        prompt_version="label_mentions/v1",
        is_about_brand=True,
        sentiment=-1,
        topic=Topic.RELIABILITY,
        is_complaint=True,
        severity=70,
        reason="Cancelled rides.",
    )
    scan = RecordedScan(recorded_at=datetime(2026, 10, 3, tzinfo=UTC), settings={}, answers=())
    return Recording(
        format=1,
        brand=name.lower(),
        name=name,
        payloads=(),
        scans=(scan,),
        labels=(label,),
        narratives=stories,
    )


def ask(task: LlmTask, prompt: str, *texts: str, brand: str = "Ola", **extra: Any) -> LlmRequest:
    mentions = [
        {
            "id": f"m{n}",
            "source": "play_review",
            "topic": "reliability",
            "severity": "70",
            "text": t,
        }
        for n, t in enumerate(texts, 1)
    ]
    return LlmRequest(
        task=task,
        prompt_version=prompt,
        variables={"brand": brand, "aliases": "", "mentions": mentions, **extra},
        shape=request_shape(TaskSettings(OPUS, Effort.LOW, 8000)),
        output_schema={},
    )


def grouping(llm: ReplayLlm, *texts: str, **extra: Any) -> dict[str, Any]:
    response = llm.complete(ask(LlmTask.GROUP_NARRATIVES, GROUPING, *texts, **extra))
    assert (response.served_model, response.hops) == (REPLAY_MODEL, ())  # no cost, no price
    answer: dict[str, Any] = json.loads(response.output or "")
    return answer


def test_texts_start_the_story_they_were_recorded_in_once_and_one_offs_join_none() -> None:
    llm = ReplayLlm([recording(story(LATE, "Cancelled rides"), story(AGAIN, "Cancelled rides"))])
    answer = grouping(llm, LATE, AGAIN, ONE_OFF)
    assert answer["new_narratives"] == [
        {"id": "r1", "label": "Cancelled rides", "summary": "Cancelled rides: what riders say."}
    ]
    assert answer["placements"] == [
        {"id": "m1", "narrative": "r1"},
        {"id": "m2", "narrative": "r1"},
        {"id": "m3", "narrative": None},  # in no story in the recording: a one-off
    ]


def test_a_text_joins_the_open_narrative_with_its_story_s_label() -> None:
    llm = ReplayLlm([recording(story(AGAIN, "Cancelled rides"), story(REFUND, "Refund delays"))])
    open_story = {"id": "n1", "mentions": "4", "label": "Cancelled rides", "text": "Started."}
    answer = grouping(llm, AGAIN, REFUND, narratives=[open_story])
    assert answer["placements"] == [
        {"id": "m1", "narrative": "n1"},
        {"id": "m2", "narrative": "r1"},
    ]
    assert [n["label"] for n in answer["new_narratives"]] == ["Refund delays"]


def test_a_story_counts_only_for_its_brand_and_the_prompt_version_that_made_it() -> None:
    ola = recording(story(LATE, "Cancelled rides"))
    uber = recording(story(LATE, "Uber surge pricing"), name="Uber")
    older = recording(story(REFUND, "Refund delays", prompt="group_narratives/v2"))
    llm = ReplayLlm([ola, uber, older])
    assert grouping(llm, LATE)["new_narratives"][0]["label"] == "Cancelled rides"
    assert grouping(llm, LATE, brand="Uber")["new_narratives"][0]["label"] == "Uber surge pricing"
    assert grouping(llm, REFUND)["placements"] == [{"id": "m1", "narrative": None}]
    assert grouping(llm, LATE, brand="Rapido")["new_narratives"] == []


@pytest.mark.parametrize(
    "task",
    sorted(t for t in SCAN_TASKS if (PROMPT_FILES / t.value).is_dir()),
    ids=str,
)
def test_every_model_task_a_scan_calls_has_a_replayed_answer(task: LlmTask) -> None:
    prompt = f"{task.value}/v1"
    answer = ReplayLlm([recording(story(LATE, "Cancelled rides"))]).complete(
        ask(task, prompt, LATE)
    )
    assert answer.served_model == REPLAY_MODEL and json.loads(answer.output or "")
