"""A model client that answers from replay recordings (`SERPSENSE_MODE=replay`): no Anthropic
key, no network, no cost (adapters/replay/recording.py, ADR-0008).

It answers with what the model said about the same text of the same brand when the scans were
recorded, and only when it was said by the prompt version asked for (provenance, AGENTS §7):
- labelling: the recorded label; a text with none is left out of the answer, so it stays "not
  labelled yet", which scores like no label at all;
- grouping: the recorded story, joining the open narrative with the same label or starting it;
  a text the recording put in no story is placed in none, as a one-off.
Nothing is made up. Every answer is recorded honestly: served by the model `replay`, with no
tokens and no cost, so no usage page shows a Claude call that didn't happen, and the exporter
can leave replayed output out of a new recording. A task with nothing recorded fails as an API
error would, without a retry. Like every client, it never logs what it is sent or answers.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from serpsense.adapters.replay.recording import (
    RecordedLabel,
    RecordedNarrative,
    Recording,
    text_id,
)
from serpsense.domain.enums import LlmTask, MentionSource
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse

REPLAY_MODEL = "replay"  # `llm_calls.served_model` of every replayed answer
LABEL_FIELDS = ("is_about_brand", "sentiment", "topic", "is_complaint", "severity", "reason")
Records = Sequence[Mapping[str, str]]
Output = TypeVar("Output", RecordedLabel, RecordedNarrative)


class ReplayLlm:
    def __init__(self, recordings: Sequence[Recording]) -> None:
        self._labels = {(r.name, label.text_id): label for r in recordings for label in r.labels}
        self._stories = {(r.name, s.text_id): s for r in recordings for s in r.narratives}

    def complete(self, request: LlmRequest) -> LlmResponse:
        brand, mentions = request.variables.get("brand"), _records(request, "mentions")
        if not isinstance(brand, str) or mentions is None:
            raise LlmCallFailed("llm.replay_unrecorded", retryable=False, latency_ms=0)
        if request.task is LlmTask.LABEL_MENTIONS:
            output: dict[str, Any] = {"labels": self._labelling(brand, mentions, request)}
        elif request.task is LlmTask.GROUP_NARRATIVES:
            output = self._grouping(brand, mentions, request)
        else:
            raise LlmCallFailed("llm.replay_unrecorded", retryable=False, latency_ms=0)
        return LlmResponse(
            served_model=REPLAY_MODEL,
            stop_reason="end_turn",
            output=json.dumps(output),
            hops=(),  # no model ran, so nothing is priced
            latency_ms=0,
        )

    def _labelling(self, brand: str, mentions: Records, request: LlmRequest) -> list[Any]:
        return [
            {"id": record["id"], **_fields(label)}
            for record in mentions
            if (label := _recorded(self._labels, brand, record, request)) is not None
        ]

    def _grouping(self, brand: str, mentions: Records, request: LlmRequest) -> dict[str, Any]:
        open_ids = {story["label"]: story["id"] for story in _records(request, "narratives") or []}
        started: dict[str, dict[str, str]] = {}
        placements: list[dict[str, str | None]] = []
        for record in mentions:
            story = _recorded(self._stories, brand, record, request)
            target = None if story is None else open_ids.get(story.label)
            if story is not None and target is None:
                new = {"id": f"r{len(started) + 1}", "label": story.label, "summary": story.summary}
                target = started.setdefault(story.label, new)["id"]
            placements.append({"id": record["id"], "narrative": target})
        return {"new_narratives": list(started.values()), "placements": placements}


def _records(request: LlmRequest, name: str) -> Records | None:
    value = request.variables.get(name)
    return None if value is None or isinstance(value, str) else value


def _recorded(
    found: Mapping[tuple[str, str], Output],
    brand: str,
    record: Mapping[str, str],
    request: LlmRequest,
) -> Output | None:
    """What the recording holds for the text, if the same prompt version made it."""
    output = found.get((brand, text_id(MentionSource(record["source"]), record["text"])))
    return (
        output if output is not None and output.prompt_version == request.prompt_version else None
    )


def _fields(label: RecordedLabel) -> dict[str, object]:
    return label.model_dump(mode="json", include=set(LABEL_FIELDS))
