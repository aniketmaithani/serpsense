"""The alert store on real Postgres: what the rules read about a scan, and alerts written once."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection, insert, select

from serpsense.adapters.db.alert_store import SqlAlertStore
from serpsense.domain.alert_rules import Fired
from serpsense.domain.enums import AlertRule, CrisisComponent, CrisisLevel, Surface
from serpsense.ports.alert_store import AlertStore
from tests.integration.db_helpers import NOW, add_brand, add_llm_call, add_scan, add_user, table
from tests.integration.test_narratives_schema import GROUPING, narrative
from tests.integration.test_scan_scores_view import scored

pytestmark = pytest.mark.integration

HOUR = timedelta(hours=1)
CALM = {c: 0 for c in CrisisComponent}
RISING = CALM | {CrisisComponent.VELOCITY: 100, CrisisComponent.AUTOCOMPLETE: 90}  # crisis 48


@pytest.fixture
def store(conn: Connection) -> AlertStore:
    return SqlAlertStore(conn)


def scans(conn: Connection, brand_id: uuid.UUID, count: int) -> list[uuid.UUID]:
    """The brand's scans, 12 hours apart, the last one now; all scored calm but the last."""
    made = []
    for n in range(count):
        at = NOW - 12 * HOUR * (count - 1 - n)
        scan_id = add_scan(conn, brand_id, status="succeeded", scheduled_for=at, created_at=at)
        scored(conn, scan_id, {Surface.NEWS: 60}, RISING if n == count - 1 else CALM)
        made.append(scan_id)
    return made


def test_the_context_of_a_scored_scan(conn: Connection, store: AlertStore) -> None:
    owner = add_user(conn)
    brand_id = add_brand(conn, owner, name="Ola", slug="ola")
    rival = add_brand(conn, owner, name="Uber", slug="uber")
    conn.execute(
        insert(table("brand_competitors")).values(brand_id=rival, competitor_brand_id=brand_id)
    )
    *earlier, latest = scans(conn, brand_id, 5)
    store.fire(earlier[0], AlertRule.LEVEL_INCREASE, at=NOW - 48 * HOUR)  # an older one ...
    store.fire(earlier[-1], AlertRule.LEVEL_INCREASE, at=NOW - 12 * HOUR)  # ... the latest wins
    store.fire(latest, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE, at=NOW)  # the scan's own: not "last"
    later = add_scan(conn, brand_id, status="running", scheduled_for=NOW + HOUR)
    store.fire(later, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE, at=NOW + HOUR)  # nor a later scan's
    story = narrative(conn, brand_id, add_llm_call(conn, owner, **GROUPING))
    spread = {"scan_id": earlier[-1], "rule": "narrative_spread", "narrative_id": story}
    conn.execute(insert(table("alerts")).values(id=uuid.uuid4(), created_at=NOW, **spread))
    hour_ago = {"status": "succeeded", "scheduled_for": NOW - HOUR, "created_at": NOW - HOUR}
    rivals = add_scan(conn, rival, **hour_ago)  # another brand's alert cools none of Ola's
    store.fire(rivals, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE, at=NOW - HOUR)

    context = store.context(latest)
    assert context is not None
    assert (context.brand_name, context.competitor, context.health, context.crisis) == (
        "Ola",
        True,
        60,
        48,
    )
    facts = context.facts
    assert (facts.at, facts.level, facts.previous) == (NOW, CrisisLevel.MEDIUM, CrisisLevel.LOW)
    assert facts.autocomplete == 90
    assert dict(facts.last) == {AlertRule.LEVEL_INCREASE: Fired(NOW - 12 * HOUR, CrisisLevel.LOW)}


def test_a_brand_warming_up_has_no_level(conn: Connection, store: AlertStore) -> None:
    brand_id = add_brand(conn, add_user(conn))
    *_, second = scans(conn, brand_id, 2)
    context = store.context(second)
    assert context is not None
    assert (context.facts.level, context.facts.previous, context.competitor) == (None, None, False)
    unscored = add_scan(conn, brand_id, status="running", scheduled_for=NOW + HOUR)
    assert store.context(unscored) is None


def test_an_alert_and_its_notification_are_written_once(
    conn: Connection, store: AlertStore
) -> None:
    owner = add_user(conn)
    (scan_id,) = scans(conn, add_brand(conn, owner), 1)
    alert_id = store.fire(scan_id, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE, at=NOW)
    assert alert_id is not None
    assert store.fire(scan_id, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE, at=NOW) is None
    assert store.notify(alert_id, title="Ola", body="A new suggestion.", at=NOW) is True
    assert store.notify(alert_id, title="Ola", body="Again.", at=NOW) is False
    notes = table("notifications")
    rows = conn.execute(select(notes.c.user_id, notes.c.alert_id, notes.c.body)).all()
    assert [tuple(row) for row in rows] == [(owner, alert_id, "A new suggestion.")]
