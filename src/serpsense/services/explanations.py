"""Explain a new alert in plain words (BUILD_PLAN §12, ADR-0008).

An alert fires from deterministic rules; this job, sent after the alert commits, asks the model to
explain it to the brand's owner from the facts the alert rests on, and stores the explanation as
model output (one per alert, so a redelivered job writes nothing). The explanation never decides
or sends anything: the alert, its notification and its email stand without it, and the email
waits a little for it (data-model §8). A model that fails, refuses, runs out of budget or isn't
configured is logged and leaves the alert unexplained. An explanation carrying a link, a
phone number, an email address or invisible characters is refused like an unusable answer (it
would go straight into the owner's email). No call is made for an alert whose brand is archived
or whose owner's account is deleted (ADR-0013). The brief is read in one unit of work, the call
made outside any, and the explanation written in another, which also nudges the outbox so a
waiting email can go at once.
"""

import uuid
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from serpsense.domain.enums import CrisisComponent, CrisisLevel, LlmTask
from serpsense.domain.llm_capabilities import UnsupportedSetting
from serpsense.domain.model_text import has_contact, has_invisible
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.explanations import AlertBrief, BriefMention
from serpsense.ports.llm_client import LlmCallFailed, PromptUnavailable, Variables
from serpsense.ports.llm_profiles import LlmProfiles
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.llm_gateway import Call, LlmBudgetExhausted, LlmGateway, LlmOutputRejected

log = get_logger(__name__)

TASK = LlmTask.EXPLAIN_CRISIS
PROMPT_VERSION = "explain_crisis/v1"
# Plain text the table holds (≤ 2000); no control characters, so nothing odd reaches an email.
Text = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=2000,
        pattern=r"^[^\x00-\x08\x0b-\x1f\x7f]+$",
    ),
]


class Explanation(BaseModel):
    explanation: Text


class Explained(StrEnum):
    EXPLAINED = "explained"
    ALREADY = "already"  # explained before: a redelivered job
    MISSING = "missing"  # no such alert
    SKIPPED = "skipped"  # the brand is archived or its owner's account deleted
    FAILED = "failed"  # the model gave nothing usable; the alert stays unexplained


FAILURES = (
    LlmCallFailed,
    LlmOutputRejected,
    LlmBudgetExhausted,
    UnsupportedSetting,
    PromptUnavailable,
)


class Explainer:
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

    def explain(self, alert_id: uuid.UUID) -> Explained:
        with self._unit_of_work() as uow:
            brief = uow.explanations.brief(alert_id)
        if brief is None:
            return Explained.MISSING
        if brief.explained:
            return Explained.ALREADY
        if brief.gone:
            return Explained.SKIPPED
        try:
            call = Call(
                task=TASK,
                prompt_version=PROMPT_VERSION,
                variables=variables(brief),
                settings=self._profiles.settings(brief.owner_id, TASK),
                user_id=brief.owner_id,
                scan_id=brief.scan_id,
            )
            answer = self._gateway.run(call, Explanation)
        except FAILURES as exc:  # the gateway recorded any call it made
            log.warning("explanation.failed", alert_id=str(alert_id), error=type(exc).__name__)
            return Explained.FAILED
        words = answer.output.explanation
        if has_contact(words) or has_invisible(words):
            log.warning(
                "explanation.refused", alert_id=str(alert_id), llm_call_id=str(answer.call_id)
            )
            return Explained.FAILED
        with self._unit_of_work() as uow:
            written = uow.explanations.record(
                alert_id,
                text=words,
                prompt_version=answer.prompt_version,
                llm_call_id=answer.call_id,
                at=self._clock.now(),
            )
            uow.jobs.dispatch_outbox()  # its email may be waiting for it
        log.info("explanation.recorded", alert_id=str(alert_id), llm_call_id=str(answer.call_id))
        return Explained.EXPLAINED if written else Explained.ALREADY


def variables(brief: AlertBrief) -> Variables:
    """The brief as the prompt's variables: numbers and names as text, mentions as records."""
    return {
        "brand": brief.brand_name,
        "competitor": "yes" if brief.competitor else "no",
        "rule": brief.rule.value,
        "level": _level(brief.level),
        "previous_level": _level(brief.previous_level),
        "crisis": str(brief.crisis),
        "health": "none" if brief.health is None else str(brief.health),
        "components": _components(brief.components),
        "story_label": brief.story_label or "",
        "story_summary": brief.story_summary or "",
        "mentions": _mentions(brief.mentions),
    }


def _level(level: CrisisLevel | None) -> str:
    return "none" if level is None else level.value


def _components(components: Mapping[CrisisComponent, int]) -> str:
    return ", ".join(f"{c.value} {components.get(c, 0)}" for c in CrisisComponent)


def _mentions(mentions: Sequence[BriefMention]) -> list[dict[str, str]]:
    return [
        {
            "source": m.source.value,
            "sentiment": "" if m.sentiment is None else str(m.sentiment),
            "text": m.text,
        }
        for m in mentions
    ]
