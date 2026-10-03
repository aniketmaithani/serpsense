"""Draft a response to a story, for a person to copy (BUILD_PLAN §13, ADR-0008).

The owner asks for a kind of draft (a holding statement, a review reply or an FAQ entry) at a
preset: standard (the owner's draft_response settings), high thinking (xhigh effort) or max. The
model is shown the story and its current mentions under short ids and must cite the ones its
draft responds to; a draft citing nothing, or an id it wasn't shown, is refused and not stored.
High thinking and max ask for a summary of the model's reasoning, kept with the draft, and are
offered only when the owner's draft model can think that hard.

Drafting runs in the request, with guards so a click can't hold a server thread for long or run
up a bill: the client's own retries are off (one attempt, within the preset's timeout); a user
has at most one draft being written at a time (a lease for the call); and high-thinking and max
drafts are capped per user per UTC day, counted from the ledger, failed calls included. The
material is read in one unit of work, the call made outside any, and the draft written in
another. A draft with invisible characters is refused. The model never sends anything; a person
copies the draft.
"""

import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from serpsense.domain.enums import DraftKind, DraftPreset, LlmTask
from serpsense.domain.llm_capabilities import (
    Effort,
    TaskSettings,
    UnsupportedSetting,
    request_shape,
)
from serpsense.domain.model_text import has_invisible
from serpsense.domain.usage import day_start
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.drafts import DraftMaterial, NewDraft
from serpsense.ports.leases import Leases
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
DAILY_THINKING = 10  # high-thinking and max drafts a user may ask for in a UTC day
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
    BUSY = "busy"  # a draft of the asker's is being written
    LIMITED = "limited"  # today's high-thinking and max drafts are used up
    UNSUPPORTED = "unsupported"  # the asker's draft model can't think this hard


@dataclass(frozen=True, kw_only=True)
class DraftPorts:
    unit_of_work: UnitOfWorkFactory
    gateway: LlmGateway
    profiles: LlmProfiles
    leases: Leases
    clock: Clock


@dataclass(frozen=True)
class _Ask:
    narrative_id: uuid.UUID
    kind: DraftKind
    preset: DraftPreset


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
    def __init__(self, ports: DraftPorts) -> None:
        self._ports = ports

    def presets(self, user_id: uuid.UUID) -> tuple[DraftPreset, ...]:
        """The presets the user's draft model can take: a model with a thinking budget can't
        go past it (Haiku's high-thinking and max budgets reach the draft's output limit)."""
        base = self._ports.profiles.settings(user_id, TASK)
        return tuple(preset for preset in DraftPreset if _takes(_settings(base, preset)))

    def draft(
        self,
        user_id: uuid.UUID,
        brand_id: uuid.UUID,
        narrative_id: uuid.UUID,
        *,
        kind: DraftKind,
        preset: DraftPreset,
    ) -> DraftResult:
        ports = self._ports
        with ports.unit_of_work() as uow:
            material = uow.drafts.material(user_id, brand_id, narrative_id, limit=MATERIAL)
            today = day_start(ports.clock.now())
            thinking = uow.drafts.thinking_drafts_since(user_id, today)
        if material is None:
            return DraftResult(Outcome.MISSING)
        if not material.mentions:
            return DraftResult(Outcome.EMPTY)
        settings = _settings(ports.profiles.settings(material.owner_id, TASK), preset)
        if not _takes(settings):
            return DraftResult(Outcome.UNSUPPORTED)
        if preset is not DraftPreset.STANDARD and thinking >= DAILY_THINKING:
            return DraftResult(Outcome.LIMITED)
        with ports.leases.hold(f"draft:{user_id}") as held:
            if not held:
                return DraftResult(Outcome.BUSY)
            return self._write(_Ask(narrative_id, kind, preset), material, settings)

    def _write(self, ask: _Ask, material: DraftMaterial, settings: TaskSettings) -> DraftResult:
        ids = {f"m{n}": m.mention_id for n, m in enumerate(material.mentions, start=1)}
        story = str(ask.narrative_id)
        try:
            answer = self._ports.gateway.run(self._call(material, ask, settings), Drafted)
        except FAILURES as exc:  # the gateway recorded any call it made
            log.warning("draft.failed", narrative_id=story, error=type(exc).__name__)
            return DraftResult(Outcome.FAILED)
        if has_invisible(answer.output.text):
            log.warning("draft.refused", narrative_id=story, llm_call_id=str(answer.call_id))
            return DraftResult(Outcome.FAILED)
        cited = list(dict.fromkeys(answer.output.cited))
        if any(key not in ids for key in cited):
            log.warning("draft.uncited", narrative_id=story, call=str(answer.call_id))
            return DraftResult(Outcome.UNCITED)
        draft_id = self._record(ask, answer, [ids[k] for k in cited])
        log.info("draft.recorded", draft_id=str(draft_id), llm_call_id=str(answer.call_id))
        return DraftResult(Outcome.DRAFTED, draft_id)

    def _call(self, material: DraftMaterial, ask: _Ask, settings: TaskSettings) -> Call:
        return Call(
            task=TASK,
            prompt_version=PROMPT_VERSION,
            variables=variables(material, ask.kind),
            settings=settings,
            user_id=material.owner_id,
            timeout_seconds=TIMEOUTS[ask.preset],
            reasoning_summary=ask.preset is not DraftPreset.STANDARD,
            max_retries=0,  # a person is waiting: one attempt, within the timeout
        )

    def _record(self, ask: _Ask, answer: Answer[Drafted], cited: list[uuid.UUID]) -> uuid.UUID:
        summary = (answer.reasoning_summary or "").strip()[:8000] or None
        draft = NewDraft(
            narrative_id=ask.narrative_id,
            kind=ask.kind,
            preset=ask.preset,
            text=answer.output.text,
            reasoning_summary=summary,
            cited=cited,
            prompt_version=answer.prompt_version,
            llm_call_id=answer.call_id,
            created_at=self._ports.clock.now(),
        )
        with self._ports.unit_of_work() as uow:
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


def _takes(settings: TaskSettings) -> bool:
    try:
        request_shape(settings)
    except UnsupportedSetting:
        return False
    return True


def _settings(base: TaskSettings, preset: DraftPreset) -> TaskSettings:
    """The owner's draft settings, with the preset's effort when it asks for more thinking."""
    effort = EFFORTS.get(preset)
    return base if effort is None else replace(base, effort=effort)
