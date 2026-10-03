"""Raising a scan's alerts: one per rule that fired, each with a notification, written once."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from serpsense.domain.alert_rules import AlertFacts
from serpsense.domain.enums import AlertRule, CrisisLevel
from serpsense.ports.alert_store import ScanAlertContext
from serpsense.services.alerts import message, raise_alerts

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
RISE, SUGGESTION = AlertRule.LEVEL_INCREASE, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE
FACTS = AlertFacts(AT, CrisisLevel.HIGH, CrisisLevel.LOW, 90, MappingProxyType({}))
OLA = ScanAlertContext(FACTS, "Ola", competitor=False, health=58, crisis=74)


class Alerts:
    """An alert store in memory: one alert per (scan, rule), one notification per alert."""

    def __init__(self, context: ScanAlertContext | None) -> None:
        self.given = context
        self.fired: dict[tuple[uuid.UUID, AlertRule], uuid.UUID] = {}
        self.told: dict[uuid.UUID, tuple[str, str]] = {}

    def context(self, scan_id: uuid.UUID) -> ScanAlertContext | None:
        return self.given

    def fire(self, scan_id: uuid.UUID, rule: AlertRule, *, at: datetime) -> uuid.UUID | None:
        if (scan_id, rule) in self.fired:
            return None
        self.fired[scan_id, rule] = uuid.uuid4()
        return self.fired[scan_id, rule]

    def notify(self, alert_id: uuid.UUID, *, title: str, body: str, at: datetime) -> bool:
        if alert_id in self.told:
            return False
        self.told[alert_id] = (title, body)
        return True


def test_each_rule_that_fires_raises_an_alert_with_a_notification_once() -> None:
    store, scan_id = Alerts(OLA), uuid.uuid4()
    assert raise_alerts(store, scan_id, at=AT) == [RISE, SUGGESTION]
    assert sorted(title for title, _ in store.told.values()) == [
        "Ola: a negative search suggestion appeared",
        "Ola: crisis level rose to high",
    ]
    assert raise_alerts(store, scan_id, at=AT) == []  # a re-run of the finish raises nothing
    assert len(store.told) == 2


def test_a_scan_without_scores_or_a_due_rule_raises_nothing() -> None:
    assert raise_alerts(Alerts(None), uuid.uuid4(), at=AT) == []
    calm = replace(OLA, facts=replace(FACTS, level=CrisisLevel.LOW, autocomplete=0))
    assert raise_alerts(Alerts(calm), uuid.uuid4(), at=AT) == []


def test_a_message_states_the_scores_and_marks_a_competitor() -> None:
    assert message(OLA, RISE) == (
        "Ola: crisis level rose to high",
        "The crisis score is 74 (high, up from low). Health is 58.",
    )
    rival = replace(OLA, brand_name="Uber", competitor=True, health=None)
    title, body = message(rival, SUGGESTION)
    assert title == "Competitor: Uber: a negative search suggestion appeared"
    assert body == (
        "People typing Uber into Google now see a negative suggestion. "
        "The crisis score is 74 (high)."
    )


def test_a_narrative_alert_isnt_told_without_its_narrative() -> None:
    with pytest.raises(ValueError, match="narrative"):
        message(OLA, AlertRule.NARRATIVE_SPREAD)
