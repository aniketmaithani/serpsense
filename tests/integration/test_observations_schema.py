"""Observation rules enforced by Postgres itself (data-model.md §5)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, insert, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add_app,
    add_location,
    add_mention,
    add_owned_brand,
    add_scan,
    table,
    violation,
)

pytestmark = pytest.mark.integration

MENTION_OBS = table("mention_observations")
RATING_OBS = table("app_rating_observations")


def observe_mention(conn: Connection, mention_id: uuid.UUID, scan_id: uuid.UUID, **kw: Any) -> None:
    conn.execute(insert(MENTION_OBS).values(mention_id=mention_id, scan_id=scan_id, **kw))


def observe_rating(conn: Connection, app_id: uuid.UUID, scan_id: uuid.UUID, **kw: Any) -> None:
    values = {"rating_hundredths": 410, "review_count": 1200, **kw}
    conn.execute(insert(RATING_OBS).values(brand_app_id=app_id, scan_id=scan_id, **values))


def test_observations_link_a_scan_to_its_own_brand(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    scan_id = add_scan(conn, brand_id)
    observe_mention(conn, add_mention(conn, brand_id), scan_id, position=1, star_rating=5)
    observe_rating(conn, add_app(conn, brand_id), scan_id, rating_hundredths=500, review_count=0)
    other_app = add_app(conn, brand_id, app_id="com.voltbox.buds")
    observe_rating(conn, other_app, scan_id, rating_hundredths=100)


def test_mention_of_another_brand_is_rejected(conn: Connection) -> None:
    mention_id = add_mention(conn, add_owned_brand(conn, "soundnest"))
    with pytest.raises(IntegrityError) as exc:
        observe_mention(conn, mention_id, add_scan(conn, add_owned_brand(conn)))
    assert violation(exc).constraint_name == "ck_mention_observations_same_brand"


def test_app_of_another_brand_is_rejected(conn: Connection) -> None:
    app_id = add_app(conn, add_owned_brand(conn, "soundnest"))
    with pytest.raises(IntegrityError) as exc:
        observe_rating(conn, app_id, add_scan(conn, add_owned_brand(conn)))
    assert violation(exc).constraint_name == "ck_app_rating_observations_same_brand"


def test_missing_mention_reports_the_foreign_key(conn: Connection) -> None:
    with pytest.raises(IntegrityError) as exc:
        observe_mention(conn, uuid.uuid4(), add_scan(conn, add_owned_brand(conn)))
    assert violation(exc).constraint_name == "fk_mention_observations_mention_id_mentions"


@pytest.mark.parametrize(
    ("values", "check"),
    [
        ({"position": 0}, "position_positive"),
        ({"star_rating": 0}, "star_rating_range"),
        ({"star_rating": 6}, "star_rating_range"),
    ],
)
def test_mention_observation_checks(conn: Connection, values: dict[str, Any], check: str) -> None:
    brand_id = add_owned_brand(conn)
    with pytest.raises(IntegrityError) as exc:
        observe_mention(conn, add_mention(conn, brand_id), add_scan(conn, brand_id), **values)
    assert violation(exc).constraint_name == f"ck_mention_observations_{check}"


@pytest.mark.parametrize(
    ("values", "check"),
    [
        ({"rating_hundredths": 99}, "rating_range"),
        ({"rating_hundredths": 501}, "rating_range"),
        ({"review_count": -1}, "review_count_non_negative"),
    ],
)
def test_app_rating_checks(conn: Connection, values: dict[str, Any], check: str) -> None:
    brand_id = add_owned_brand(conn)
    with pytest.raises(IntegrityError) as exc:
        observe_rating(conn, add_app(conn, brand_id), add_scan(conn, brand_id), **values)
    assert violation(exc).constraint_name == f"ck_app_rating_observations_{check}"


def test_a_mention_or_app_is_observed_once_per_scan(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    mention_id, scan_id = add_mention(conn, brand_id), add_scan(conn, brand_id)
    observe_mention(conn, mention_id, scan_id)
    with pytest.raises(IntegrityError) as exc, conn.begin_nested():
        observe_mention(conn, mention_id, scan_id, position=2)
    assert violation(exc).constraint_name == "pk_mention_observations"
    app_id = add_app(conn, brand_id)
    observe_rating(conn, app_id, scan_id)
    with pytest.raises(IntegrityError) as exc:
        observe_rating(conn, app_id, scan_id, review_count=1201)
    assert violation(exc).constraint_name == "pk_app_rating_observations"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
@pytest.mark.parametrize("name", ["mention_observations", "app_rating_observations"])
def test_observations_are_append_only(conn: Connection, name: str, mutation: str) -> None:
    brand_id = add_owned_brand(conn)
    scan_id = add_scan(conn, brand_id)
    if name == "mention_observations":
        observe_mention(conn, add_mention(conn, brand_id), scan_id)
    else:
        observe_rating(conn, add_app(conn, brand_id), scan_id)
    rows = table(name)
    this_scan = rows.c.scan_id == scan_id
    statements: dict[str, Executable] = {
        "update": update(rows).where(this_scan).values(scan_id=scan_id),
        "delete": delete(rows).where(this_scan),
        "truncate": text(f"TRUNCATE {name}"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


@pytest.mark.parametrize(
    "column",
    ["source", "identity_key", "brand_id", "brand_location_id", "brand_app_id", "created_at"],
)
def test_mention_identity_cannot_change(conn: Connection, column: str) -> None:
    mentions = table("mentions")
    brand_id = add_owned_brand(conn)
    edit = update(mentions).where(mentions.c.id == add_mention(conn, brand_id))
    conn.execute(edit.values(text="edited"))  # content may be scrubbed
    changed = {
        "source": "top_story",
        "identity_key": "other",
        "brand_id": add_owned_brand(conn, "soundnest"),
        "brand_location_id": add_location(conn, brand_id),
        "brand_app_id": add_app(conn, brand_id),
        "created_at": NOW + timedelta(days=1),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(edit.values({column: changed[column]}))
    assert violation(exc).constraint_name == "ck_mentions_identity_immutable"


@pytest.mark.parametrize(
    ("name", "column", "value"),
    [
        ("brand_apps", "app_id", "com.voltbox.buds"),
        ("brand_apps", "brand_id", None),
        ("brand_locations", "query", "VoltBox Koramangala"),
        ("brand_locations", "brand_id", None),
    ],
)
def test_app_and_location_identity_cannot_change(
    conn: Connection, name: str, column: str, value: Any
) -> None:
    rows = table(name)
    row_id = (add_app if name == "brand_apps" else add_location)(conn, add_owned_brand(conn))
    edit = update(rows).where(rows.c.id == row_id)
    if name == "brand_locations":  # re-resolving or re-casing keeps the same place
        resolved = {"resolved_data_id": "0x1:0x2", "resolved_at": NOW}
        conn.execute(edit.values(query="VOLTBOX INDIRANAGAR", **resolved))
    with pytest.raises(IntegrityError) as exc:
        conn.execute(edit.values({column: value or add_owned_brand(conn, "soundnest")}))
    assert violation(exc).constraint_name == f"ck_{name}_identity_immutable"


def test_observed_mention_cannot_be_deleted(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    mention_id = add_mention(conn, brand_id)
    observe_mention(conn, mention_id, add_scan(conn, brand_id))
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table("mentions")).where(table("mentions").c.id == mention_id))
    assert violation(exc).constraint_name == "fk_mention_observations_mention_id_mentions"
