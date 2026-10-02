"""Brand attribute rules enforced by Postgres itself (data-model.md §3)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, delete, insert, select
from sqlalchemy.exc import DataError, IntegrityError

from tests.integration.db_helpers import NOW, add, add_brand, add_user, table, violation

pytestmark = pytest.mark.integration

INVALID_TEXT_REPRESENTATION = "22P02"
PLAY = "google_play"
# Tables whose per-brand value is unique ignoring case (citext): table -> value column.
CASE_INSENSITIVE = {
    "brand_aliases": "alias",
    "brand_watch_terms": "term",
    "brand_locations": "query",
}


def brand(conn: Connection, slug: str = "voltbox") -> uuid.UUID:
    return add_brand(conn, add_user(conn, f"{slug}@example.com"), slug=slug)


def add_language(conn: Connection, brand_id: uuid.UUID, code: str) -> None:
    conn.execute(insert(table("brand_languages")).values(brand_id=brand_id, language_code=code))


def add_app(conn: Connection, brand_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values = {"store": "google_play", "app_id": "com.voltbox.connect", **overrides}
    return add(conn, table("brand_apps"), brand_id=brand_id, **values)


@pytest.mark.parametrize(("name", "column"), CASE_INSENSITIVE.items())
def test_value_is_unique_per_brand_ignoring_case(conn: Connection, name: str, column: str) -> None:
    first, second = brand(conn, "voltbox"), brand(conn, "soundnest")
    add(conn, table(name), brand_id=first, **{column: "Battery Blast"})
    add(conn, table(name), brand_id=second, **{column: "battery blast"})
    with pytest.raises(IntegrityError) as exc:
        add(conn, table(name), brand_id=first, **{column: "BATTERY BLAST"})
    assert violation(exc).constraint_name == f"uq_{name}_brand_id_{column}"


@pytest.mark.parametrize(
    ("name", "values", "constraint"),
    [
        ("brand_aliases", {"alias": "  "}, "ck_brand_aliases_alias_length"),
        ("brand_aliases", {"alias": "x" * 121}, "ck_brand_aliases_alias_length"),
        ("brand_aliases", {"alias": "Volt "}, "ck_brand_aliases_alias_length"),
        ("brand_watch_terms", {"term": " refund"}, "ck_brand_watch_terms_term_length"),
        ("brand_locations", {"query": "VoltBox\t"}, "ck_brand_locations_query_length"),
        ("brand_watch_terms", {"term": "\t"}, "ck_brand_watch_terms_term_length"),
        ("brand_watch_terms", {"term": "x" * 81}, "ck_brand_watch_terms_term_length"),
        ("brand_locations", {"query": "x" * 201}, "ck_brand_locations_query_length"),
        ("brand_locations", {"query": "\n "}, "ck_brand_locations_query_length"),
        (
            "brand_locations",
            {"query": "VoltBox Indiranagar", "resolved_data_id": "0x3bae1:0x1"},
            "ck_brand_locations_resolution_together",
        ),
        (
            "brand_locations",
            {"query": "VoltBox Indiranagar", "resolved_at": NOW},
            "ck_brand_locations_resolution_together",
        ),
        ("brand_apps", {"store": PLAY, "app_id": "com voltbox"}, "ck_brand_apps_app_id_format"),
        ("brand_apps", {"store": PLAY, "app_id": "x" * 256}, "ck_brand_apps_app_id_format"),
    ],
)
def test_attribute_checks(
    conn: Connection, name: str, values: dict[str, Any], constraint: str
) -> None:
    with pytest.raises(IntegrityError) as exc:
        add(conn, table(name), brand_id=brand(conn), **values)
    assert violation(exc).constraint_name == constraint


def test_location_resolution_round_trips(conn: Connection) -> None:
    locations = table("brand_locations")
    location_id = add(
        conn,
        locations,
        brand_id=brand(conn),
        query="VoltBox service centre Indiranagar",
        resolved_data_id="0x3bae13:0x9f1c",
        resolved_at=NOW,
    )
    row = conn.execute(select(locations).where(locations.c.id == location_id)).one()
    assert (row.resolved_data_id, row.resolved_at) == ("0x3bae13:0x9f1c", NOW)


@pytest.mark.parametrize("code", ["en", "hi", "zh-cn", "en-in", "fil"])
def test_valid_language_codes_are_accepted(conn: Connection, code: str) -> None:
    add_language(conn, brand(conn), code)


@pytest.mark.parametrize("code", ["EN", "english", "e", "en_IN", "en-"])
def test_invalid_language_codes_are_rejected(conn: Connection, code: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        add_language(conn, brand(conn), code)
    assert violation(exc).constraint_name == "ck_brand_languages_language_code_format"


def test_language_is_listed_once_per_brand(conn: Connection) -> None:
    brand_id = brand(conn)
    add_language(conn, brand_id, "hi")
    with pytest.raises(IntegrityError) as exc:
        add_language(conn, brand_id, "hi")
    assert violation(exc).constraint_name == "pk_brand_languages"


def test_app_is_unique_per_brand_store_and_id(conn: Connection) -> None:
    brand_id = brand(conn)
    add_app(conn, brand_id)
    add_app(conn, brand_id, app_id="com.voltbox.buds")
    with pytest.raises(IntegrityError) as exc:
        add_app(conn, brand_id)
    assert violation(exc).constraint_name == "uq_brand_apps_brand_id_store_app_id"


def test_unknown_app_store_is_rejected(conn: Connection) -> None:
    with pytest.raises(DataError) as exc:
        add_app(conn, brand(conn), store="apple_app_store")
    assert exc.value.orig.diag.sqlstate == INVALID_TEXT_REPRESENTATION  # type: ignore[union-attr]  # orig is the DBAPI error


@pytest.mark.parametrize(
    ("name", "values"),
    [
        ("brand_aliases", {"alias": "Volt Box"}),
        ("brand_watch_terms", {"term": "refund"}),
        ("brand_apps", {"store": "google_play", "app_id": "com.voltbox.connect"}),
        ("brand_locations", {"query": "VoltBox Indiranagar"}),
        ("brand_languages", {"language_code": "hi"}),
    ],
)
def test_brand_with_attributes_cannot_be_deleted(
    conn: Connection, name: str, values: dict[str, Any]
) -> None:
    brand_id = brand(conn)
    if name == "brand_languages":  # composite key, no surrogate id
        add_language(conn, brand_id, values["language_code"])
    else:
        add(conn, table(name), brand_id=brand_id, **values)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table("brands")).where(table("brands").c.id == brand_id))
    assert violation(exc).constraint_name == f"fk_{name}_brand_id_brands"
