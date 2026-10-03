"""A brand's crisis tuning, as its page shows and saves it (docs/scoring.md). The owner's only:
another user's or an archived brand reads as missing. A save writes a version only when it
changes something; the brand's levels follow it at once (they are derived), its alerts from the
next scan."""

import uuid
from dataclasses import dataclass

from serpsense.domain.scoring.tuning import CrisisTuning
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.brand_settings import Saved

log = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class TuningView:
    brand_id: uuid.UUID
    name: str
    tuning: CrisisTuning


class CrisisTuner:
    def __init__(self, unit_of_work: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    def view(self, user_id: uuid.UUID, brand_id: uuid.UUID) -> TuningView | None:
        with self._unit_of_work() as uow:
            inputs = uow.schedules.scan_inputs(user_id, brand_id, as_of=self._clock.now())
            if inputs is None:
                return None
            tuning = uow.brands.crisis_tuning(brand_id)
        return TuningView(brand_id=brand_id, name=inputs.name, tuning=tuning)

    def save(self, user_id: uuid.UUID, brand_id: uuid.UUID, tuning: CrisisTuning) -> Saved:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            if uow.schedules.scan_inputs(user_id, brand_id, as_of=now) is None:
                return Saved.MISSING
            changed = uow.brands.set_crisis_tuning(brand_id, tuning, at=now)
        if changed:
            log.info("crisis_tuning.saved", user_id=str(user_id), brand_id=str(brand_id))
        return Saved.SAVED if changed else Saved.UNCHANGED
