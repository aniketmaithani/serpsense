"""Group a brand's unfavourable mentions into narratives with the model (BUILD_PLAN §7, §10).

Runs after labelling, in the same scan. Nothing is sent unless a waiting mention was labelled
after the brand's last successful grouping call, so one-offs aren't sent every scan. Otherwise
the waiting mentions go in batches of 40 under short ids (m1, …), each offered the brand's open
narratives (n1, …), read afresh so a story the batch before started can grow. The model places
each mention in an open narrative, a new one or none (a one-off); each batch is stored in its own
unit of work, none open while the model thinks. No batch starts past the scan's deadline.
Failures go as in labelling: a failed batch is skipped (its mentions wait for the next scan); two
in a row, one no retry can fix, or a spent budget stop the run. Whether a story is spreading is
decided by a deterministic rule, not the model (domain/alert_rules.py).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from serpsense.domain.enums import LlmTask
from serpsense.domain.labelling import PROMPTS
from serpsense.domain.llm_capabilities import TaskSettings
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.llm_client import LlmCallFailed
from serpsense.ports.narrative_store import UngroupedMention
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.grouping_answer import (
    Grouping,
    mention_record,
    narrative_record,
    placements,
)
from serpsense.services.labelling import BrandContext
from serpsense.services.llm_gateway import Call, LlmBudgetExhausted, LlmGateway, LlmOutputRejected

log = get_logger(__name__)

TASK = LlmTask.GROUP_NARRATIVES
PROMPT_VERSION = "group_narratives/v1"
BATCH = 40
MOST_PER_RUN = 120  # three calls at most
SEEN_FOR = timedelta(days=7)
OPEN_FOR, MOST_OPEN = timedelta(days=14), 30
STOP_AFTER = 2  # failed batches in a row


@dataclass(frozen=True)
class Grouped:
    placed: int
    failed_batches: int
    budget_exhausted: bool = False
    out_of_time: bool = False  # batches were left for the next scan at the deadline

    @property
    def complete(self) -> bool:
        return self.failed_batches == 0 and not self.budget_exhausted and not self.out_of_time


class Grouper:
    def __init__(self, unit_of_work: UnitOfWorkFactory, gateway: LlmGateway, clock: Clock) -> None:
        self._unit_of_work = unit_of_work
        self._gateway = gateway
        self._clock = clock

    def group(
        self,
        brand: BrandContext,
        settings: TaskSettings,
        scan_id: uuid.UUID | None = None,
        *,
        deadline: datetime | None = None,
    ) -> Grouped:
        waiting = self._waiting(brand)
        placed = failed = in_a_row = 0
        for start in range(0, len(waiting), BATCH):
            if deadline is not None and self._clock.now() >= deadline:
                log.warning("grouping.out_of_time", brand_id=str(brand.brand_id), placed=placed)
                return Grouped(placed, failed, out_of_time=True)
            try:
                placed += self._batch(brand, settings, scan_id, waiting[start : start + BATCH])
                in_a_row = 0
            except (LlmCallFailed, LlmOutputRejected) as exc:
                failed, in_a_row = failed + 1, in_a_row + 1  # the gateway recorded it
                hopeless = isinstance(exc, LlmCallFailed) and not exc.retryable
                if hopeless or in_a_row >= STOP_AFTER:
                    log.warning("grouping.stopped", brand_id=str(brand.brand_id), failed=failed)
                    return Grouped(placed, failed)
            except LlmBudgetExhausted:
                return Grouped(placed, failed, budget_exhausted=True)
        log.info("grouping.completed", brand_id=str(brand.brand_id), placed=placed, failed=failed)
        return Grouped(placed, failed)

    def _waiting(self, brand: BrandContext) -> Sequence[UngroupedMention]:
        """The mentions to send: none unless one was labelled after the last grouping call."""
        with self._unit_of_work() as uow:
            since = self._clock.now() - SEEN_FOR
            waiting = uow.narratives.ungrouped(
                brand.brand_id, first_seen_since=since, prompts=PROMPTS, limit=MOST_PER_RUN
            )
            considered = uow.narratives.considered_until(brand.brand_id)
        if considered is not None and all(m.labelled_at <= considered for m in waiting):
            return []
        return waiting

    def _batch(
        self,
        brand: BrandContext,
        settings: TaskSettings,
        scan_id: uuid.UUID | None,
        batch: Sequence[UngroupedMention],
    ) -> int:
        with self._unit_of_work() as uow:
            active = self._clock.now() - OPEN_FOR
            stories = uow.narratives.open(brand.brand_id, active_since=active, limit=MOST_OPEN)
        known = {f"n{n}": story for n, story in enumerate(stories, start=1)}
        mentions = {f"m{n}": mention for n, mention in enumerate(batch, start=1)}
        call = Call(
            task=TASK,
            prompt_version=PROMPT_VERSION,
            variables={
                "brand": brand.name,
                "aliases": ", ".join(brand.aliases),
                "narratives": [narrative_record(key, story) for key, story in known.items()],
                "mentions": [mention_record(key, mention) for key, mention in mentions.items()],
            },
            settings=settings,
            user_id=brand.owner_id,
            scan_id=scan_id,
        )
        answer = self._gateway.run(call, Grouping)
        placed = placements(answer.output, brand.brand_id, known, mentions)
        if placed.dropped or placed.ignored:
            log.warning(
                "grouping.placements_dropped",
                llm_call_id=str(answer.call_id),
                dropped=placed.dropped,
                ignored=placed.ignored,
            )
        with self._unit_of_work() as uow:
            return uow.narratives.record(
                placed.narratives,
                placed.assignments,
                prompt_version=PROMPT_VERSION,
                llm_call_id=answer.call_id,
                at=self._clock.now(),
            )
