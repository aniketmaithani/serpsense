"""Label a brand's recent mentions with the model (BUILD_PLAN §10 step 6, ADR-0008).

The texts still to label are read in one unit of work, sent in batches of 25 through the gateway
under short ids (m1, m2, …), and each batch's labels are stored in a unit of work of their own,
so no transaction stays open while the model thinks. A batch that fails or gives no usable
answer is skipped: its texts stay pending and the brand's next scan picks them up. Two failed
batches in a row, or a failure no retry can fix, stop the run, so a model that is down can't
hold a scan past its time limit; so does a spent budget. Settings the model rejects and a
missing prompt file are configuration errors and are raised. The model only labels; scores and
alerts are computed from its labels by deterministic rules.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from serpsense.domain.enums import LlmTask, Topic
from serpsense.domain.labelling import PROMPTS, sources_for
from serpsense.domain.llm_capabilities import TaskSettings
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.enrichment_store import MentionLabel, PendingText
from serpsense.ports.llm_client import LlmCallFailed
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.llm_gateway import Call, LlmBudgetExhausted, LlmGateway, LlmOutputRejected

log = get_logger(__name__)

TASK = LlmTask.LABEL_MENTIONS
PROMPT_VERSION = PROMPTS[LlmTask.LABEL_MENTIONS]
BATCH = 25
MOST_PER_RUN = 200  # eight calls at most
SEEN_FOR = timedelta(days=7)
STOP_AFTER = 2  # failed batches in a row


class Label(BaseModel):
    """One mention's label, as the prompt asks for it (relevance decided first)."""

    id: str
    is_about_brand: bool
    sentiment: Literal[-1, 0, 1]
    topic: Topic
    is_complaint: bool
    severity: int = Field(ge=0, le=100)
    # The prompt asks for 200 characters; the table holds 500, so a longer reason doesn't
    # sink its whole batch. A blank one is invalid output, as the table would refuse it.
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class Labels(BaseModel):
    labels: list[Label]


@dataclass(frozen=True)
class BrandContext:
    brand_id: uuid.UUID
    owner_id: uuid.UUID  # the calls are billed to the brand's owner
    name: str
    aliases: tuple[str, ...] = ()
    not_the_brand: str = ""  # same-named businesses that aren't this brand


@dataclass(frozen=True)
class Labelled:
    labelled: int
    failed_batches: int
    budget_exhausted: bool = False


class Labeller:
    def __init__(self, unit_of_work: UnitOfWorkFactory, gateway: LlmGateway, clock: Clock) -> None:
        self._unit_of_work = unit_of_work
        self._gateway = gateway
        self._clock = clock

    def label(
        self, brand: BrandContext, settings: TaskSettings, scan_id: uuid.UUID | None = None
    ) -> Labelled:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            pending = uow.enrichments.pending(
                brand.brand_id,
                prompt_version=PROMPT_VERSION,
                sources=sources_for(TASK),
                seen_since=now - SEEN_FOR,
                limit=MOST_PER_RUN,
            )
        labelled = failed = in_a_row = 0
        for start in range(0, len(pending), BATCH):
            batch = pending[start : start + BATCH]
            try:
                labelled += self._batch(brand, settings, scan_id, batch)
                in_a_row = 0
            except (LlmCallFailed, LlmOutputRejected) as exc:
                failed, in_a_row = failed + 1, in_a_row + 1  # the gateway recorded it
                hopeless = isinstance(exc, LlmCallFailed) and not exc.retryable
                if hopeless or in_a_row >= STOP_AFTER:
                    log.warning("labelling.stopped", brand_id=str(brand.brand_id), failed=failed)
                    return Labelled(labelled, failed)
            except LlmBudgetExhausted:
                return Labelled(labelled, failed, budget_exhausted=True)
        log.info(
            "labelling.completed", brand_id=str(brand.brand_id), labelled=labelled, failed=failed
        )
        return Labelled(labelled, failed)

    def _batch(
        self,
        brand: BrandContext,
        settings: TaskSettings,
        scan_id: uuid.UUID | None,
        batch: Sequence[PendingText],
    ) -> int:
        texts = {f"m{n}": text for n, text in enumerate(batch, start=1)}
        call = Call(
            task=TASK,
            prompt_version=PROMPT_VERSION,
            variables={
                "brand": brand.name,
                "aliases": ", ".join(brand.aliases),
                "not_the_brand": brand.not_the_brand,
                "mentions": [_record(key, text) for key, text in texts.items()],
            },
            settings=settings,
            user_id=brand.owner_id,
            scan_id=scan_id,
        )
        answer = self._gateway.run(call, Labels)
        labels = [_label(texts[out.id], out) for out in answer.output.labels if out.id in texts]
        with self._unit_of_work() as uow:
            return uow.enrichments.record(
                _first_per_text(labels),
                prompt_version=PROMPT_VERSION,
                llm_call_id=answer.call_id,
                at=self._clock.now(),
            )


def _record(key: str, text: PendingText) -> dict[str, str]:
    record = {"id": key, "source": text.source.value, "text": text.text}
    if text.language_code:
        record["language"] = text.language_code
    return record


def _label(text: PendingText, out: Label) -> MentionLabel:
    """A text that only shares the brand's name gets the fixed values the prompt asks for,
    whatever else the model said, so it can't move a score."""
    about = out.is_about_brand
    return MentionLabel(
        mention_id=text.mention_id,
        revision=text.revision,
        sentiment=out.sentiment if about else 0,
        severity=out.severity if about else 0,
        topic=out.topic if about else Topic.OTHER,
        is_complaint=out.is_complaint and about,
        is_about_brand=about,
        reason=out.reason,
    )


def _first_per_text(labels: list[MentionLabel]) -> list[MentionLabel]:
    """A model that labels one id twice gets its first answer kept."""
    seen: dict[tuple[uuid.UUID, int], MentionLabel] = {}
    for label in labels:
        seen.setdefault((label.mention_id, label.revision), label)
    return list(seen.values())
