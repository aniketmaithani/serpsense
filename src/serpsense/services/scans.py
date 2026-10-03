"""Run one scan, from its claim to its finish (BUILD_PLAN §10, ADR-0005).

A worker is handed a scan id, and Postgres is the source of truth: the scan is claimed with a
compare-and-set, so a redelivered message finds nothing to do. Then the brand behind it is read,
and the scan is skipped if the brand was archived or its owner's account deleted, or if the
owner's searches this month can't cover the most it may make. Otherwise its surfaces are
collected and kept, and it finishes by how its surfaces went, in a last unit of work that checks
the brand again. Each stage has a unit of work of its own, and no SerpApi call happens inside one.

A stage that raises fails the scan (`stage_failed`) and the error propagates. The scan task has
no soft time limit and never retries: each external call already did, and collection keeps to
its own deadline (#47).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from serpsense.domain import usage
from serpsense.domain.enums import ScanStatus
from serpsense.domain.estimator import BrandFacts, estimate
from serpsense.domain.scan_state import Transition, TransitionReason, finished
from serpsense.domain.settings.search import SearchSettings
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.collector import App, Subject, Target
from serpsense.ports.scan_targets import ScanTarget
from serpsense.ports.search_ledger import SearchUsage
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.collection import Collected, CollectorRunner
from serpsense.services.harvest import harvest, keep

log = get_logger(__name__)

S, R = ScanStatus, TransitionReason
CLAIM = Transition(S.QUEUED, S.RUNNING, R.CLAIMED)
STAGE_FAILED = Transition(S.RUNNING, S.FAILED, R.STAGE_FAILED)


@dataclass(frozen=True)
class ScanPorts:
    unit_of_work: UnitOfWorkFactory
    collector: CollectorRunner
    usage: SearchUsage
    clock: Clock


@dataclass(frozen=True)
class ScanLimits:
    time_limit: timedelta  # how long collection may take from the claim
    monthly_searches: int  # a user's monthly budget when none was set


class ScanService:
    def __init__(self, ports: ScanPorts, limits: ScanLimits) -> None:
        self._ports, self._limits = ports, limits

    def run(self, scan_id: uuid.UUID) -> ScanStatus | None:
        """The status the scan finished in; None when another worker has it, or had it."""
        claimed = self._ports.clock.now()
        with self._ports.unit_of_work() as uow:
            if not uow.scans.move(scan_id, CLAIM, at=claimed):
                log.info("scan.claim_lost", scan_id=str(scan_id))
                return None
            target = uow.targets.for_scan(scan_id)
        try:
            ending = self._stages(_present(target), claimed)
        except Exception as exc:
            log.error("scan.stage_failed", scan_id=str(scan_id), error=type(exc).__name__)
            self._fail(scan_id)
            raise
        return self._finish(scan_id, ending)

    def _stages(self, target: ScanTarget, claimed: datetime) -> Transition:
        gone = _gone(target)
        if gone is not None:
            return gone
        settings = SearchSettings.model_validate(dict(target.settings_snapshot))
        aim = _aim(target, settings)
        most = min(estimate(settings, _facts(aim)), settings.max_searches)
        if not self._affordable(target.owner_id, most, claimed):
            return Transition(S.RUNNING, S.SKIPPED, R.BUDGET_EXHAUSTED)
        deadline = claimed + self._limits.time_limit
        collected = self._ports.collector.collect(
            aim, user_id=target.owner_id, scan_id=target.scan_id, deadline=deadline
        )
        with self._ports.unit_of_work() as uow:
            kept = keep(uow, harvest(target.scan_id, collected), at=self._ports.clock.now())
        new, revised = kept.new_mentions, kept.revised
        log.info("scan.collected", scan_id=str(target.scan_id), new=new, revised=revised)
        outcomes = (outcome.outcome for outcome in collected.outcomes.values())
        return finished(outcomes, answered=_answered(collected), enrichment_failed=False)

    def _affordable(self, user_id: uuid.UUID, searches: int, at: datetime) -> bool:
        budget = self._ports.usage.monthly_budget(user_id, at=at)
        used = self._ports.usage.live_calls(since=usage.month_start(at), user_id=user_id)
        left = (self._limits.monthly_searches if budget is None else budget) - used
        return searches <= left

    def _finish(self, scan_id: uuid.UUID, ending: Transition) -> ScanStatus | None:
        """Move the scan to its ending, or to skipped if its brand went meanwhile. The alerts a
        scan raises will be written in this same unit of work."""
        with self._ports.unit_of_work() as uow:
            target = uow.targets.for_scan(scan_id)
            ending = (_gone(target) if target else None) or ending
            moved = uow.scans.move(scan_id, ending, at=self._ports.clock.now())
        status, reason = ending.to_status, ending.reason
        if not moved:  # the sweep timed it out meanwhile
            log.warning("scan.finish_lost", scan_id=str(scan_id), reason=reason)
            return None
        log.info("scan.finished", scan_id=str(scan_id), status=status, reason=reason)
        return status

    def _fail(self, scan_id: uuid.UUID) -> None:
        """Finish a scan as stage_failed; if even that fails, log it and leave the scan to the
        sweep, so the stage's own error is the one that propagates."""
        try:
            with self._ports.unit_of_work() as uow:
                uow.scans.move(scan_id, STAGE_FAILED, at=self._ports.clock.now())
        except Exception as exc:  # noqa: BLE001 (the caller re-raises the stage's own error)
            log.error("scan.finish_failed", scan_id=str(scan_id), error=type(exc).__name__)


def _present(target: ScanTarget | None) -> ScanTarget:
    if target is None:
        raise LookupError("the scan's row is gone")
    return target


def _gone(target: ScanTarget) -> Transition | None:
    if target.owner_deleted:
        return Transition(S.RUNNING, S.SKIPPED, R.ACCOUNT_DELETED)
    if target.brand_archived:
        return Transition(S.RUNNING, S.SKIPPED, R.BRAND_ARCHIVED)
    return None


def _answered(collected: Collected) -> bool:
    return bool(collected.readings)


def _aim(target: ScanTarget, settings: SearchSettings) -> Target:
    """The collectors' target; values the brand pages should have refused raise ValueError."""
    return Target(
        Subject(target.brand.brand_id, target.brand.name),
        settings,
        competitors=tuple(Subject(c.brand_id, c.name) for c in target.competitors),
        apps=tuple(App(app.brand_app_id, app.package) for app in target.apps),
    )


def _facts(aim: Target) -> BrandFacts:
    return BrandFacts(apps=len(aim.apps), locations=0)  # places come with the Maps collector
