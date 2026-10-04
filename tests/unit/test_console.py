"""What the operator console does behind its door: reads as of now, records decisions
(ADR-0014)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from structlog.testing import capture_logs

from serpsense.domain.enums import AccessDecision
from serpsense.ports.console import BrandRow, RequestRow, Totals
from serpsense.services.console import Console, ConsoleGate, GatePorts
from tests.fakes import FixedClock
from tests.unit.test_console_gate import AT, KEYS, Limiter, Work

pytestmark = pytest.mark.unit


@dataclass
class Decisions:
    known: set[uuid.UUID] = field(default_factory=set)
    made: list[tuple[uuid.UUID, AccessDecision, datetime]] = field(default_factory=list)

    def decide(self, request_id: uuid.UUID, decision: AccessDecision, *, at: datetime) -> bool:
        self.made.append((request_id, decision, at))
        return request_id in self.known


@dataclass
class DeskWork(Work):
    access: Decisions = field(default_factory=Decisions)


@dataclass
class Reads:
    asked_at: list[datetime] = field(default_factory=list)

    def totals(self, at: datetime) -> Totals:
        self.asked_at.append(at)
        return Totals(
            users=1,
            brands=2,
            archived_brands=0,
            brands_this_week=2,
            scans_today=3,
            searches_this_month=4,
            pending_requests=5,
        )

    def requests(self) -> list[RequestRow]:
        return []

    def brands(self) -> list[BrandRow]:
        return []


def test_the_console_reads_as_of_now_and_records_decisions() -> None:
    decisions = Decisions({uuid.uuid4()})
    work, clock, reads = DeskWork(access=decisions), FixedClock(AT), Reads()
    desk = Console(ConsoleGate(GatePorts(work, Limiter(), clock), KEYS), reads, work, clock)
    assert desk.snapshot().totals.pending_requests == 5 and reads.asked_at == [AT]
    (known,) = decisions.known
    with capture_logs() as logs:
        assert desk.decide(known, AccessDecision.APPROVED)
        assert not desk.decide(uuid.uuid4(), AccessDecision.REJECTED)  # no such request
    assert [(log["event"], log["decision"]) for log in logs] == [("access.decided", "approved")]
    assert decisions.made[0] == (known, AccessDecision.APPROVED, AT)
