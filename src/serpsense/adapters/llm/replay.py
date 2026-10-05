"""A model client that answers from replay recordings (`SERPSENSE_MODE=replay`): no Anthropic
key, no network, no cost (adapters/replay/recording.py, ADR-0008).

It answers with what the model said about the same text of the same brand when the scans were
recorded, and only when it was said by the prompt version asked for (provenance, AGENTS §7):
- labelling: the recorded label; a text with none is left out of the answer, so it stays "not
  labelled yet", which scores like no label at all;
- grouping: the recorded story, joining the open narrative with the same label or starting it;
  a text the recording put in no story is placed in none, as a one-off;
- explaining an alert: the words recorded for its rule, the level reached and its story;
- drafting: the reply recorded for the story and the kind of draft, citing the first mentions
  it was shown (the drafter shows the worst first).
Nothing is made up. Every answer is recorded honestly: served by the model `replay`, with no
tokens and no cost, so no usage page shows a Claude call that didn't happen, and the exporter
can leave replayed output out of a new recording. A task with nothing recorded fails as an API
error would, without a retry. Like every client, it never logs what it is sent or answers.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from serpsense.adapters.replay.recording import (
    RecordedDraft,
    RecordedExplanation,
    RecordedLabel,
    RecordedNarrative,
    Recording,
    text_id,
)
from serpsense.domain.enums import LlmTask, MentionSource
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse

REPLAY_MODEL = "replay"  # `llm_calls.served_model` of every replayed answer
CITED = 3  # mentions a replayed draft cites
LABEL_FIELDS = ("is_about_brand", "sentiment", "topic", "is_complaint", "severity", "reason")
Records = Sequence[Mapping[str, str]]
Output = TypeVar("Output", RecordedLabel, RecordedNarrative)


class ReplayLlm:
    def __init__(self, recordings: Sequence[Recording]) -> None:
        self._labels = {(r.name, label.text_id): label for r in recordings for label in r.labels}
        self._stories = {(r.name, s.text_id): s for r in recordings for s in r.narratives}
        self._explanations: dict[tuple[str, str, str, str], RecordedExplanation] = {
            (r.name, e.rule.value, e.level, e.story_label): e
            for r in recordings
            for e in r.explanations
        }
        self._drafts = {
            (r.name, d.story_label, d.kind.value): d for r in recordings for d in r.drafts
        }

    def complete(self, request: LlmRequest) -> LlmResponse:
        brand, mentions = request.variables.get("brand"), _records(request, "mentions")
        if not isinstance(brand, str) or mentions is None:
            raise _unrecorded()
        if request.task is LlmTask.LABEL_MENTIONS:
            output: dict[str, Any] = {"labels": self._labelling(brand, mentions, request)}
        elif request.task is LlmTask.GROUP_NARRATIVES:
            output = self._grouping(brand, mentions, request)
        elif request.task is LlmTask.EXPLAIN_CRISIS:
            output = {"explanation": self._explanation(brand, request).text}
        elif request.task is LlmTask.DRAFT_RESPONSE:
            cited = [record["id"] for record in mentions[:CITED]]
            output = {"text": self._draft(brand, request).text, "cited": cited}
        else:
            raise _unrecorded()
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

    def _explanation(self, brand: str, request: LlmRequest) -> RecordedExplanation:
        asked = request.variables
        rule, level = str(asked.get("rule", "")), str(asked.get("level", ""))
        key = (brand, rule, level, str(asked.get("story_label", "")))
        return _same_prompt(self._explanations.get(key), request)

    def _draft(self, brand: str, request: LlmRequest) -> RecordedDraft:
        asked = request.variables
        key = (brand, str(asked.get("story_label", "")), str(asked.get("kind", "")))
        return _same_prompt(self._drafts.get(key), request)

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


Said = TypeVar("Said", RecordedExplanation, RecordedDraft)


def _same_prompt(found: Said | None, request: LlmRequest) -> Said:
    """Recorded output, if the prompt version asked for made it; otherwise the call fails."""
    if found is None or found.prompt_version != request.prompt_version:
        raise _unrecorded()
    return found


def _unrecorded() -> LlmCallFailed:
    return LlmCallFailed("llm.replay_unrecorded", retryable=False, latency_ms=0)


def _fields(label: RecordedLabel) -> dict[str, object]:
    return label.model_dump(mode="json", include=set(LABEL_FIELDS))
