"""A model client that answers from replay recordings (`SERPSENSE_MODE=replay`): no Anthropic
key, no network, no cost (adapters/replay/recording.py, ADR-0008).

Labelling is answered with what the model said about the same text of the same brand when the
scans were recorded, and only when it was said by the prompt version asked for (provenance,
AGENTS §7). Nothing is made up: a text with no such label is left out of the answer, so it stays
"not labelled yet", which scores like no label at all. Every answer is recorded honestly: served
by the model `replay`, with no tokens and no cost, so no usage page shows a Claude call that
didn't happen, and the exporter can leave replayed output out of a new recording. A task with
nothing recorded fails as an API error would, without a retry. Like every client, it never logs
what it is sent or answers.
"""

import json
from collections.abc import Mapping, Sequence

from serpsense.adapters.replay.recording import RecordedLabel, Recording, text_id
from serpsense.domain.enums import LlmTask, MentionSource
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse

REPLAY_MODEL = "replay"  # `llm_calls.served_model` of every replayed answer
LABEL_FIELDS = ("is_about_brand", "sentiment", "topic", "is_complaint", "severity", "reason")


class ReplayLlm:
    def __init__(self, recordings: Sequence[Recording]) -> None:
        self._labels = {
            (recording.name, label.text_id): label
            for recording in recordings
            for label in recording.labels
        }

    def complete(self, request: LlmRequest) -> LlmResponse:
        mentions, brand = request.variables.get("mentions"), request.variables.get("brand")
        if request.task is not LlmTask.LABEL_MENTIONS or not isinstance(brand, str):
            raise LlmCallFailed("llm.replay_unrecorded", retryable=False, latency_ms=0)
        if mentions is None or isinstance(mentions, str):
            raise LlmCallFailed("llm.replay_unrecorded", retryable=False, latency_ms=0)
        labels = [
            {"id": record["id"], **_fields(label)}
            for record in mentions
            if (label := self._label(brand, record, request.prompt_version)) is not None
        ]
        return LlmResponse(
            served_model=REPLAY_MODEL,
            stop_reason="end_turn",
            output=json.dumps({"labels": labels}),
            hops=(),  # no model ran, so nothing is priced
            latency_ms=0,
        )

    def _label(
        self, brand: str, record: Mapping[str, str], prompt_version: str
    ) -> RecordedLabel | None:
        key = text_id(MentionSource(record["source"]), record["text"])
        label = self._labels.get((brand, key))
        return label if label is not None and label.prompt_version == prompt_version else None


def _fields(label: RecordedLabel) -> dict[str, object]:
    return label.model_dump(mode="json", include=set(LABEL_FIELDS))
