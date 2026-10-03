"""Raise a finished scan's alerts (BUILD_PLAN §12).

The deterministic rules in `domain/alert_rules.py` decide; each rule that fires writes one alert,
one in-app notification and one email to the brand's owner (through the outbox, ADR-0010), all in
the unit of work that finishes the scan, so they commit with its ending and its scores. The two
channels say the same thing. A competitor's alert says so in its title. The model's explanation
comes later and never decides anything (ADR-0008).
"""

import uuid
from datetime import datetime
from typing import assert_never

from serpsense.domain import alert_rules
from serpsense.domain.enums import AlertRule, CrisisLevel
from serpsense.ports.alert_store import ScanAlertContext
from serpsense.ports.unit_of_work import UnitOfWork


def raise_alerts(uow: UnitOfWork, scan_id: uuid.UUID, *, at: datetime) -> list[AlertRule]:
    """The rules that fired for the scan now; none for a scan that wasn't scored."""
    store = uow.alerts
    context = store.context(scan_id)
    if context is None:
        return []
    raised = []
    for rule in alert_rules.due(context.facts):
        alert_id = store.fire(scan_id, rule, at=at)
        if alert_id is None:  # raised already, by an earlier run of this finish
            continue
        title, body = message(context, rule)
        store.notify(alert_id, title=title, body=body, at=at)
        uow.outbox.add_alert_email(alert_id, data={"title": title, "body": body}, at=at)
        raised.append(rule)
    return raised


def message(context: ScanAlertContext, rule: AlertRule) -> tuple[str, str]:
    """A notification's title and body: plain facts from the scores, no model output."""
    brand = context.brand_name
    name = f"Competitor: {brand}" if context.competitor else brand
    level = context.facts.level or CrisisLevel.LOW
    health = "" if context.health is None else f" Health is {context.health}."
    score = f"The crisis score is {context.crisis}"
    match rule:
        case AlertRule.LEVEL_INCREASE:
            was = context.facts.previous or CrisisLevel.LOW
            title = f"{name}: crisis level rose to {level}"
            body = f"{score} ({level}, up from {was}).{health}"
        case AlertRule.NEW_NEGATIVE_AUTOCOMPLETE:
            title = f"{name}: a negative search suggestion appeared"
            body = (
                f"People typing {brand} into Google now see a negative suggestion. "
                f"{score} ({level}).{health}"
            )
        case AlertRule.NARRATIVE_SPREAD:
            raise ValueError("a narrative alert is told with its narrative")
        case _:
            assert_never(rule)
    return title, body
