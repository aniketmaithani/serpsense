"""A scan's finish raises its alerts, after its scores, in the same unit of work."""

from datetime import UTC, datetime
from types import MappingProxyType

import pytest
from structlog.testing import capture_logs

from serpsense.domain.alert_rules import AlertFacts
from serpsense.domain.enums import AlertRule, CrisisLevel
from serpsense.ports.alert_store import ScanAlertContext
from tests.fakes import ScriptedSearch
from tests.unit.test_scans import MOST, S, scan

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
RISING = AlertFacts(AT, CrisisLevel.HIGH, CrisisLevel.LOW, 0, MappingProxyType({}))
OLA = ScanAlertContext(RISING, "Ola", competitor=False, health=58, crisis=74)


def test_a_scored_scan_raises_its_alerts_as_it_finishes() -> None:
    run = scan(ScriptedSearch())
    run.uow.alerts.given = OLA
    with capture_logs() as logs:
        assert run.service.run(run.scan_id) is S.SUCCEEDED
    assert [rule for _, rule in run.uow.alerts.fired] == [AlertRule.LEVEL_INCREASE]
    assert [title for title, _ in run.uow.alerts.told.values()] == [
        "Ola: crisis level rose to high"
    ]
    raised = [entry for entry in logs if entry["event"] == "alert.raised"]
    assert [entry["rule"] for entry in raised] == [AlertRule.LEVEL_INCREASE]


def test_a_scan_that_isnt_scored_raises_no_alert() -> None:
    run = scan(ScriptedSearch(), used=1500 - MOST + 1)  # skipped for its budget
    run.uow.alerts.given = OLA
    assert run.service.run(run.scan_id) is S.SKIPPED
    assert run.uow.alerts.fired == {}
