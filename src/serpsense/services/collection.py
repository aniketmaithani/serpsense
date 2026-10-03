"""Collect a scan's surfaces: every collector's leads through the search service (BUILD_PLAN §10).

Leads run on a bounded thread pool (ADR-0002: synchronous code, parallel SerpApi calls); the
follow-ups an answer names run after it on the same thread, in the order they are named. No
search starts past the deadline, so a scan can't outlive its time limit without relying on
Celery's soft limit (#47).

A search that fails, or finds its engine's circuit open, drops only its own lead (the circuit is
per engine); a spent budget or a passed deadline ends the lead's whole chain. Any other error (the
ledger, a collector bug) propagates, and leads still queued are cancelled. Payloads go from the
search service to their collector unread. Call it outside any unit of work: the search
service's ledger writes each call in a transaction of its own, from the pool's threads.
"""

import contextvars
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Protocol

from serpsense.domain.collection import STOPS, Outcome, surface_outcome
from serpsense.domain.enums import SerpCallOutcome, Surface, SurfaceOutcome
from serpsense.domain.mention import SURFACE
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.collector import Collector, Lead, Reading, Target
from serpsense.ports.search_provider import SearchFailed, SearchRequest
from serpsense.services.search import SearchResult, SearchSkipped

log = get_logger(__name__)

DEADLINE_PASSED = "scan.deadline_passed"  # a surface's error code when the scan ran out of time
WORKERS = 4  # parallel SerpApi calls per scan (BUILD_PLAN §6.1)


class Searcher(Protocol):
    """Called from several threads at once, as are the collectors."""

    def search(
        self, request: SearchRequest, *, user_id: uuid.UUID, scan_id: uuid.UUID | None = None
    ) -> SearchResult: ...


@dataclass(frozen=True)
class Collected:
    """How every surface went, and every answer read, in the order the leads were planned."""

    outcomes: Mapping[Surface, Outcome]
    readings: tuple[tuple[Lead, Reading], ...]


@dataclass(frozen=True)
class _Stop:
    outcome: Outcome
    surfaces: frozenset[Surface]  # what the leads left unanswered would have served


@dataclass(frozen=True)
class _Chain:
    readings: tuple[tuple[Lead, Reading], ...]
    stops: tuple[_Stop, ...]


@dataclass(frozen=True)
class _Scan:
    user_id: uuid.UUID
    scan_id: uuid.UUID
    deadline: datetime
    owners: Mapping[Surface, int]  # the collector, by place, that enables each surface


class CollectorRunner:
    def __init__(
        self,
        collectors: Sequence[Collector],
        searcher: Searcher,
        clock: Clock,
        *,
        workers: int = WORKERS,
    ) -> None:
        self._collectors, self._searcher = tuple(collectors), searcher
        self._clock, self._workers = clock, workers

    def collect(
        self, target: Target, *, user_id: uuid.UUID, scan_id: uuid.UUID, deadline: datetime
    ) -> Collected:
        """Raises ValueError when two collectors enable one surface, or a lead serves a surface
        its collector doesn't enable."""
        if deadline.utcoffset() is None:
            raise ValueError("the deadline must be timezone-aware")
        owners = _owners(self._collectors, target)
        plan = [(n, lead) for n, c in enumerate(self._collectors) for lead in c.leads(target)]
        for n, lead in plan:
            _check(owners, n, lead)
        scan = _Scan(user_id, scan_id, deadline, owners)
        chains = self._run(plan, scan)
        by_collector: list[list[_Chain]] = [[] for _ in self._collectors]
        for (n, _), chain in zip(plan, chains, strict=True):
            by_collector[n].append(chain)
        outcomes = {
            surface: _outcome(surface, by_collector[owners[surface]])
            if surface in owners
            else Outcome(SurfaceOutcome.DISABLED)
            for surface in Surface
        }
        for surface, stop in outcomes.items():
            if stop.outcome in STOPS:
                log.info(
                    "surface.stopped", surface=surface, outcome=stop.outcome, code=stop.error_code
                )
        readings = tuple(r for chain in chains for r in chain.readings)
        return Collected(MappingProxyType(outcomes), readings)

    def _run(self, plan: list[tuple[int, Lead]], scan: _Scan) -> list[_Chain]:
        pool = ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="collect")
        try:
            futures = [
                pool.submit(contextvars.copy_context().run, self._follow, n, lead, scan)
                for n, lead in plan
            ]
            return [future.result() for future in futures]
        finally:  # after an error, leads still queued are cancelled
            pool.shutdown(wait=True, cancel_futures=True)

    def _follow(self, n: int, lead: Lead, scan: _Scan) -> _Chain:
        readings: list[tuple[Lead, Reading]] = []
        stops: list[_Stop] = []
        waiting = deque([lead])
        while waiting:
            current = waiting.popleft()
            ending = self._ending(scan)
            if ending is not None:
                stops.append(_Stop(ending, _served([current, *waiting])))
                break
            failure = self._ask(n, current, scan, readings)
            if failure is None:
                for follow_up in readings[-1][1].follow_ups:
                    _check(scan.owners, n, follow_up)
                    waiting.append(follow_up)
            elif failure.outcome is SurfaceOutcome.BUDGET_EXHAUSTED:  # a budget is spent
                stops.append(_Stop(failure, _served([current, *waiting])))
                break
            else:  # one engine failed or is closed: only this lead is lost
                stops.append(_Stop(failure, _served([current])))
        return _Chain(tuple(readings), tuple(stops))

    def _ending(self, scan: _Scan) -> Outcome | None:
        if self._clock.now() >= scan.deadline:
            return Outcome(SurfaceOutcome.FAILED, DEADLINE_PASSED)
        return None

    def _ask(
        self, n: int, lead: Lead, scan: _Scan, readings: list[tuple[Lead, Reading]]
    ) -> Outcome | None:
        try:
            result = self._searcher.search(lead.request, user_id=scan.user_id, scan_id=scan.scan_id)
        except SearchFailed as failure:
            return Outcome(SurfaceOutcome.FAILED, failure.code.value)
        except SearchSkipped as skipped:
            if skipped.outcome is SerpCallOutcome.CIRCUIT_OPEN:
                return Outcome(SurfaceOutcome.CIRCUIT_OPEN)
            return Outcome(SurfaceOutcome.BUDGET_EXHAUSTED)
        readings.append((lead, self._collectors[n].read(lead, result.payload)))
        return None


def _owners(collectors: Sequence[Collector], target: Target) -> dict[Surface, int]:
    owners: dict[Surface, int] = {}
    for n, collector in enumerate(collectors):
        for surface in collector.enabled(target):
            if surface in owners:
                raise ValueError(f"two collectors enable {surface}")
            owners[surface] = n
    return owners


def _check(owners: Mapping[Surface, int], n: int, lead: Lead) -> None:
    if any(owners.get(surface) != n for surface in (lead.surface, *lead.also)):
        raise ValueError(f"a lead for {lead.surface} serves a surface its collector doesn't enable")


def _served(leads: Sequence[Lead]) -> frozenset[Surface]:
    return frozenset(s for lead in leads for s in (lead.surface, *lead.also))


def _outcome(surface: Surface, chains: Sequence[_Chain]) -> Outcome:
    stops = [stop.outcome for c in chains for stop in c.stops if surface in stop.surfaces]
    shown = any(_shown(surface, lead, reading) for c in chains for lead, reading in c.readings)
    return surface_outcome(stops, shown=shown)


def _shown(surface: Surface, lead: Lead, reading: Reading) -> bool:
    if any(SURFACE[mention.source] is surface for mention in reading.mentions):
        return True
    return lead.surface is surface and surface not in reading.not_shown
