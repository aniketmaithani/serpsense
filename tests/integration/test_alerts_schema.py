"""Alert and notification rules enforced by Postgres itself (data-model.md §8)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add,
    add_brand,
    add_llm_call,
    add_scan,
    add_user,
    table,
    violation,
)
from tests.integration.test_narratives_schema import GROUPING, narrative

pytestmark = pytest.mark.integration

ALERTS, NOTIFICATIONS, READS = table("alerts"), table("notifications"), table("notification_reads")


def alert(conn: Connection, scan_id: uuid.UUID, **values: Any) -> uuid.UUID:
    return add(
        conn, ALERTS, scan_id=scan_id, **{"rule": "level_increase", "created_at": NOW, **values}
    )


def notify(conn: Connection, user_id: uuid.UUID, **values: Any) -> uuid.UUID:
    text_ = {"title": "Ola: crisis level is high", "body": "Velocity and press rose.", **values}
    return add(conn, NOTIFICATIONS, user_id=user_id, created_at=NOW, **text_)


def story(conn: Connection, brand_id: uuid.UUID, owner: uuid.UUID) -> uuid.UUID:
    return narrative(conn, brand_id, add_llm_call(conn, owner, **GROUPING))


def setup(conn: Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """An owner, their brand and one of its scans."""
    owner = add_user(conn)
    brand_id = add_brand(conn, owner)
    return owner, brand_id, add_scan(conn, brand_id, status="succeeded")


def test_an_alert_fires_once_per_scan_rule_and_narrative(conn: Connection) -> None:
    owner, brand_id, scan_id = setup(conn)
    alert(conn, scan_id)
    alert(conn, scan_id, rule="new_negative_autocomplete")
    alert(conn, scan_id, rule="narrative_spread", narrative_id=story(conn, brand_id, owner))
    with pytest.raises(IntegrityError) as exc:
        alert(conn, scan_id)  # no narrative twice: nulls are not distinct
    assert violation(exc).constraint_name == "uq_alerts_scan_id_rule_narrative_id"


@pytest.mark.parametrize(
    ("rule", "with_narrative"), [("narrative_spread", False), ("level_increase", True)]
)
def test_only_a_narrative_alert_names_a_narrative(
    conn: Connection, rule: str, with_narrative: bool
) -> None:
    owner, brand_id, scan_id = setup(conn)
    named = story(conn, brand_id, owner) if with_narrative else None
    with pytest.raises(IntegrityError) as exc:
        alert(conn, scan_id, rule=rule, narrative_id=named)
    assert violation(exc).constraint_name == "ck_alerts_narrative_iff_spread"


def test_a_narrative_alert_names_a_narrative_of_the_scans_brand(conn: Connection) -> None:
    owner, _, scan_id = setup(conn)
    rival = story(conn, add_brand(conn, owner, slug="uber"), owner)  # the owner's other brand
    with pytest.raises(IntegrityError) as exc:
        alert(conn, scan_id, rule="narrative_spread", narrative_id=rival)
    assert violation(exc).constraint_name == "ck_alerts_narrative_of_brand"


def test_a_notification_about_an_alert_is_written_once(conn: Connection) -> None:
    owner, _, scan_id = setup(conn)
    alert_id = alert(conn, scan_id)
    notify(conn, owner, alert_id=alert_id)
    with pytest.raises(IntegrityError) as exc:
        notify(conn, owner, alert_id=alert_id)
    assert violation(exc).constraint_name == "uq_notifications_alert_id_user_id"


def test_a_notification_about_an_alert_goes_to_the_brands_owner(conn: Connection) -> None:
    _, _, scan_id = setup(conn)
    stranger = add_user(conn, "someone@example.com")
    notify(conn, stranger)  # a notification about no alert, as often as needed
    notify(conn, stranger)
    assert conn.execute(select(func.count()).select_from(NOTIFICATIONS)).scalar_one() == 2
    with pytest.raises(IntegrityError) as exc:
        notify(conn, stranger, alert_id=alert(conn, scan_id))
    assert violation(exc).constraint_name == "ck_notifications_alert_owner"


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"title": "x" * 201}, "title_length"),
        ({"body": " "}, "body_length"),
    ],
)
def test_notification_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        notify(conn, add_user(conn), **overrides)
    assert violation(exc).constraint_name == f"ck_notifications_{check}"


@pytest.mark.parametrize("table_name", ["alerts", "notifications", "notification_reads"])
@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_alerts_and_notifications_are_append_only(
    conn: Connection, table_name: str, mutation: str
) -> None:
    owner, _, scan_id = setup(conn)
    notification_id = notify(conn, owner, alert_id=alert(conn, scan_id))
    conn.execute(READS.insert().values(notification_id=notification_id, read_at=NOW))
    target = table(table_name)
    column = "read_at" if table_name == "notification_reads" else "created_at"
    statements: dict[str, Executable] = {
        "update": update(target).values({column: func.now()}),
        "delete": delete(target),
        "truncate": text(f"TRUNCATE {table_name} CASCADE"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION
