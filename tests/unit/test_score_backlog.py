"""Scoring the scans that finished before scoring existed: oldest first, once, and no alerts."""

import uuid
from datetime import UTC, datetime

import pytest
from structlog.testing import capture_logs

from serpsense.domain.enums import MentionSource
from serpsense.domain.scoring.scan import Observed, ScoreInputs
from serpsense.services.scoring_run import score_backlog
from tests.fakes import FakeUnitOfWork, FixedClock, InMemoryScans, StaticSchedules
from tests.unit.test_scan_alerts import OLA as RISING

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)


def test_the_backlog_is_scored_oldest_first_once_without_alerts() -> None:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
    oldest, newest = uuid.uuid4(), uuid.uuid4()
    uow.scores.backlog = [oldest, newest]
    uow.alerts.given = RISING  # its rules would fire, were they asked
    assert score_backlog(lambda: uow, FixedClock(NOW)) == 2
    assert [scan_id for scan_id, _ in uow.scores.asked] == [oldest, newest]
    assert {scan_id: at for scan_id, (_, _, at) in uow.scores.recorded.items()} == {
        oldest: NOW,
        newest: NOW,
    }
    assert uow.alerts.fired == {}  # old news raises no alert
    assert score_backlog(lambda: uow, FixedClock(NOW)) == 0


def test_a_scan_that_cant_be_scored_stops_the_backlog_and_is_named() -> None:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
    poisoned = uuid.uuid4()
    uow.scores.backlog = [poisoned]
    uow.scores.given = ScoreInputs(NOW, (Observed(MentionSource.NEWS, 2, published_at=NOW),))
    with capture_logs() as logs, pytest.raises(ValueError, match="sentiment"):
        score_backlog(lambda: uow, FixedClock(NOW))
    failed = [entry for entry in logs if entry["event"] == "scan.score_failed"]
    assert [entry["scan_id"] for entry in failed] == [str(poisoned)]
