"""Raising a scan's alerts: one per rule that fired, each with a notification and an email,
written once."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from serpsense.domain.alert_rules import AlertFacts, Story
from serpsense.domain.enums import AlertRule, CrisisLevel, MentionSource
from serpsense.ports.alert_store import ScanAlertContext
from serpsense.services.alerts import message, raise_alerts
from tests.fakes import FakeUnitOfWork, InMemoryScans, StaticSchedules

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
RISE, SUGGESTION = AlertRule.LEVEL_INCREASE, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE
FACTS = AlertFacts(AT, CrisisLevel.HIGH, CrisisLevel.LOW, 90, MappingProxyType({}))
OLA = ScanAlertContext(FACTS, "Ola", competitor=False, health=58, crisis=74)


def unit_of_work(context: ScanAlertContext | None) -> FakeUnitOfWork:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules([]))
    uow.alerts.given = context
    return uow


def test_each_rule_that_fires_raises_an_alert_a_notification_and_an_email_once() -> None:
    uow, scan_id = unit_of_work(OLA), uuid.uuid4()
    with uow:
        assert raise_alerts(uow, scan_id, at=AT) == [RISE, SUGGESTION]
    titles = ["Ola: a negative search suggestion appeared", "Ola: crisis level rose to high"]
    assert sorted(title for title, _ in uow.alerts.told.values()) == titles
    emails = uow.outbox.alert_emails
    assert set(emails) == set(uow.alerts.told)  # one email per alert ...
    for alert_id, (title, body) in uow.alerts.told.items():  # ... saying what the app says
        assert emails[alert_id] == {"title": title, "body": body}
    assert sorted(uow.sent.explanations) == sorted(uow.alerts.told)  # asked for after commit
    with uow:
        assert raise_alerts(uow, scan_id, at=AT) == []  # a re-run of the finish raises nothing
    assert (len(uow.alerts.told), len(emails)) == (2, 2)


def test_a_scan_without_scores_or_a_due_rule_raises_nothing() -> None:
    calm = replace(OLA, facts=replace(FACTS, level=CrisisLevel.LOW, autocomplete=0))
    for context in (None, calm):
        uow = unit_of_work(context)
        with uow:
            assert raise_alerts(uow, uuid.uuid4(), at=AT) == []
        assert uow.outbox.alert_emails == {}


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


def spread(label: str) -> Story:
    sources = {MentionSource.PLAY_REVIEW: 4, MentionSource.NEWS: 1, MentionSource.TOP_STORY: 1}
    return Story(uuid.uuid4(), label, MappingProxyType(sources))


def test_each_spreading_story_raises_its_own_alert_once() -> None:
    cash, refunds = spread("Drivers demand cash"), spread("Refunds pending")
    calm = replace(FACTS, previous=CrisisLevel.HIGH, autocomplete=0, stories=(cash, refunds))
    uow, scan_id = unit_of_work(replace(OLA, facts=calm)), uuid.uuid4()
    with uow:
        assert raise_alerts(uow, scan_id, at=AT) == [AlertRule.NARRATIVE_SPREAD] * 2
    assert set(uow.alerts.fired) == {
        (scan_id, AlertRule.NARRATIVE_SPREAD, cash.narrative_id),
        (scan_id, AlertRule.NARRATIVE_SPREAD, refunds.narrative_id),
    }
    assert len(uow.outbox.alert_emails) == 2
    with uow:
        assert raise_alerts(uow, scan_id, at=AT) == []


def test_a_spread_message_gives_the_counts_and_marks_the_label_as_the_models() -> None:
    assert message(OLA, AlertRule.NARRATIVE_SPREAD, spread("Drivers demand cash")) == (
        "Ola: a story is spreading",
        'The story "Drivers demand cash" (labelled by AI) now has 6 mentions on 3 surfaces '
        "(news, play, search_page). The crisis score is 74 (high). Health is 58.",
    )


def test_a_narrative_alert_isnt_told_without_its_narrative() -> None:
    with pytest.raises(ValueError, match="narrative"):
        message(OLA, AlertRule.NARRATIVE_SPREAD)
