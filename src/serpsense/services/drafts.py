"""Draft a response to a story, for a person to copy (BUILD_PLAN §13, ADR-0008).

The owner asks for a kind of draft (a holding statement, a review reply or an FAQ entry) at a
preset: standard (the owner's draft_response settings), high thinking (xhigh effort) or max. The
model is shown the story and its current mentions under short ids and must cite the ones its
draft responds to; a draft citing nothing, or an id it wasn't shown, is refused and not stored.
High thinking and max ask for a summary of the model's reasoning, kept with the draft. Drafting
runs in the request: the material is read in one unit of work, the call made outside any, and
the draft written in another. The model never sends anything; a person copies the draft.
"""

import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from serpsense.domain.enums import DraftKind, DraftPreset, LlmTask
from serpsense.domain.llm_capabilities import Effort, TaskSettings, UnsupportedSetting
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.drafts import DraftMaterial, NewDraft
from serpsense.ports.llm_client import LlmCallFailed, PromptUnavailable, Variables
from serpsense.ports.llm_profiles import LlmProfiles
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.llm_gateway import (
    Answer,
    Call,
    LlmBudgetExhausted,
    LlmGateway,
    LlmOutputRejected,
)

log = get_logger(__name__)

TASK = LlmTask.DRAFT_RESPONSE
PROMPT_VERSION = "draft_response/v1"
MATERIAL = 25  # mentions shown to the model, worst first
EFFORTS = {DraftPreset.HIGH_THINKING: Effort.XHIGH, DraftPreset.MAX: Effort.MAX}
TIMEOUTS = {DraftPreset.STANDARD: 90.0, DraftPreset.HIGH_THINKING: 180.0, DraftPreset.MAX: 240.0}
PLAIN = r"^[^\x00-\x08\x0b-\x1f\x7f]+$"  # no control characters but tab and newline
DraftText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000, pattern=PLAIN)
]


class Drafted(BaseModel):
    text: DraftText
    cited: list[str] = Field(min_length=1, max_length=MATERIAL)


class Outcome(StrEnum):
    DRAFTED = "drafted"
    MISSING = "missing"  # not a narrative of this brand of the asker's
    EMPTY = "empty"  # the story has no current mentions to respond to
    FAILED = "failed"  # the model gave nothing usable (or isn't available)
    UNCITED = "uncited"  # the draft cited an id it wasn't shown, or none


@dataclass(frozen=True)
class DraftResult:
    outcome: Outcome
    draft_id: uuid.UUID | None = None


FAILURES = (
    LlmCallFailed,
    LlmOutputRejected,
    LlmBudgetExhausted,
    UnsupportedSetting,
    PromptUnavailable,
)


class Drafter:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        gateway: LlmGateway,
        profiles: LlmProfiles,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._gateway = gateway
        self._profiles = profiles
        self._clock = clock

    def draft(
        self,
        user_id: uuid.UUID,
        brand_id: uuid.UUID,
        narrative_id: uuid.UUID,
        *,
        kind: DraftKind,
        preset: DraftPreset,
    ) -> DraftResult:
        with self._unit_of_work() as uow:
            material = uow.drafts.material(user_id, brand_id, narrative_id, limit=MATERIAL)
        if material is None:
            return DraftResult(Outcome.MISSING)
        if not material.mentions:
            return DraftResult(Outcome.EMPTY)
        ids = {f"m{n}": m.mention_id for n, m in enumerate(material.mentions, start=1)}
        try:
            answer = self._gateway.run(self._call(material, kind, preset), Drafted)
        except FAILURES as exc:  # the gateway recorded any call it made
            log.warning("draft.failed", narrative_id=str(narrative_id), error=type(exc).__name__)
            return DraftResult(Outcome.FAILED)
        cited = list(dict.fromkeys(answer.output.cited))
        if any(key not in ids for key in cited):
            log.warning("draft.uncited", narrative_id=str(narrative_id), call=str(answer.call_id))
            return DraftResult(Outcome.UNCITED)
        draft_id = self._record(narrative_id, kind, preset, answer, [ids[k] for k in cited])
        log.info("draft.recorded", draft_id=str(draft_id), llm_call_id=str(answer.call_id))
        return DraftResult(Outcome.DRAFTED, draft_id)

    def _call(self, material: DraftMaterial, kind: DraftKind, preset: DraftPreset) -> Call:
        return Call(
            task=TASK,
            prompt_version=PROMPT_VERSION,
            variables=variables(material, kind),
            settings=_settings(self._profiles.settings(material.owner_id, TASK), preset),
            user_id=material.owner_id,
            timeout_seconds=TIMEOUTS[preset],
            reasoning_summary=preset is not DraftPreset.STANDARD,
        )

    def _record(
        self,
        narrative_id: uuid.UUID,
        kind: DraftKind,
        preset: DraftPreset,
        answer: Answer[Drafted],
        cited: list[uuid.UUID],
    ) -> uuid.UUID:
        summary = (answer.reasoning_summary or "").strip()[:8000] or None
        draft = NewDraft(
            narrative_id=narrative_id,
            kind=kind,
            preset=preset,
            text=answer.output.text,
            reasoning_summary=summary,
            cited=cited,
            prompt_version=answer.prompt_version,
            llm_call_id=answer.call_id,
            created_at=self._clock.now(),
        )
        with self._unit_of_work() as uow:
            return uow.drafts.record(draft)


def variables(material: DraftMaterial, kind: DraftKind) -> Variables:
    """The story as the prompt's variables, its mentions under short ids (m1, m2, …)."""
    mentions = [
        {
            "id": f"m{n}",
            "source": m.source.value,
            "sentiment": "" if m.sentiment is None else str(m.sentiment),
            "text": m.text,
        }
        for n, m in enumerate(material.mentions, start=1)
    ]
    return {
        "brand": material.brand_name,
        "kind": kind.value,
        "story_label": material.story_label,
        "story_summary": material.story_summary,
        "mentions": mentions,
    }


def _settings(base: TaskSettings, preset: DraftPreset) -> TaskSettings:
    """The owner's draft settings, with the preset's effort when it asks for more thinking."""
    effort = EFFORTS.get(preset)
    return base if effort is None else replace(base, effort=effort)
