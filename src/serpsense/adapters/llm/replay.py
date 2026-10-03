"""A model client that answers from replay recordings (`SERPSENSE_MODE=replay`): no Anthropic
key, no network, no cost (adapters/replay/recording.py).

Labelling is answered with the label the model gave the same text when the scans were recorded,
found by its source and text (`text_id`). A text the recording has no label for is answered as
not about the brand, with a reason that says why, so it can't move a score and a replayed scan
always finishes. Each answer is reported as the requested model with no tokens, so the gateway
records it in the ledger at no cost. A task with nothing recorded fails as an API error would,
without a retry. Like every client, it never logs what it is sent or answers.
"""

import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from serpsense.adapters.replay.recording import RecordedLabel, Recording, text_id
from serpsense.domain.enums import LlmTask, MentionSource
from serpsense.domain.llm_pricing import Hop, TokenUsage
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest, LlmResponse

UNRECORDED = MappingProxyType(
    {
        "is_about_brand": False,
        "sentiment": 0,
        "topic": "other",
        "is_complaint": False,
        "severity": 0,
        "reason": "Replay mode: no label was recorded for this text, so it is left out.",
    }
)
LABEL_FIELDS = ("is_about_brand", "sentiment", "topic", "is_complaint", "severity", "reason")
NO_TOKENS = TokenUsage(input=0, output=0, cache_read=0, cache_write=0)


class ReplayLlm:
    def __init__(self, recordings: Sequence[Recording]) -> None:
        self._labels = {label.text_id: label for r in recordings for label in r.labels}

    def complete(self, request: LlmRequest) -> LlmResponse:
        mentions = request.variables.get("mentions")
        if (
            request.task is not LlmTask.LABEL_MENTIONS
            or mentions is None
            or isinstance(mentions, str)
        ):
            raise LlmCallFailed("llm.replay_unrecorded", retryable=False, latency_ms=0)
        labels = [self._answer(record) for record in mentions]
        model = request.shape.model
        return LlmResponse(
            served_model=model,
            stop_reason="end_turn",
            output=json.dumps({"labels": labels}),
            hops=(Hop(model, NO_TOKENS),),
            latency_ms=0,
        )

    def _answer(self, record: Mapping[str, str]) -> dict[str, object]:
        label = self._labels.get(text_id(MentionSource(record["source"]), record["text"]))
        return {"id": record["id"], **(_fields(label) if label else UNRECORDED)}


def _fields(label: RecordedLabel) -> dict[str, object]:
    return label.model_dump(mode="json", include=set(LABEL_FIELDS))
