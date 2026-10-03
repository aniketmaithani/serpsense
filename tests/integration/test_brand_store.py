"""Writing brands, their apps, competitors, schedules and settings, on real Postgres."""

import uuid
from datetime import time, timedelta
from types import MappingProxyType

import pytest
from sqlalchemy import Connection, func, select
from sqlalchemy.exc import DBAPIError

from serpsense.adapters.db.brand_store import SqlBrandStore
from serpsense.domain.enums import AppStore
from serpsense.domain.schedule import Schedule
from serpsense.ports.brand_store import NewBrand
from tests.integration.db_helpers import NOW, add_user, table

pytestmark = pytest.mark.integration


def count(conn: Connection, name: str) -> int:
    return conn.execute(select(func.count()).select_from(table(name))).scalar_one()


def test_a_brand_is_found_by_its_owner_and_slug_or_created(conn: Connection) -> None:
    store, owner, other = SqlBrandStore(conn), add_user(conn), add_user(conn, "b@example.com")
    ola = store.brand(NewBrand(owner, "Ola", "ola", NOW))
    assert store.brand(NewBrand(owner, "Ola Cabs", "ola", NOW)) == ola  # the slug decides
    assert store.brand(NewBrand(other, "Ola", "ola", NOW)) != ola  # another owner's brand
    assert count(conn, "brands") == 2


def test_aliases_apps_and_competitors_are_added_once(conn: Connection) -> None:
    store, owner = SqlBrandStore(conn), add_user(conn)
    ola, uber = (store.brand(NewBrand(owner, name, name.lower(), NOW)) for name in ("Ola", "Uber"))
    assert store.add_alias(ola, "Ola Cabs") and not store.add_alias(ola, "OLA CABS")  # citext
    cabs = store.add_app(ola, AppStore.GOOGLE_PLAY, "com.olacabs.customer")
    assert store.add_app(ola, AppStore.GOOGLE_PLAY, "com.olacabs.customer") == cabs
    assert store.link_competitor(ola, uber) and not store.link_competitor(ola, uber)
    assert (count(conn, "brand_aliases"), count(conn, "brand_apps")) == (1, 1)
    assert count(conn, "brand_competitors") == 1


def test_a_brand_has_at_most_four_competitors(conn: Connection) -> None:
    store, owner = SqlBrandStore(conn), add_user(conn)
    ola = store.brand(NewBrand(owner, "Ola", "ola", NOW))
    for n in range(4):
        assert store.link_competitor(ola, store.brand(NewBrand(owner, f"R{n}", f"r{n}", NOW)))
    with pytest.raises(DBAPIError):
        store.link_competitor(ola, store.brand(NewBrand(owner, "R4", "r4", NOW)))


def test_a_schedule_or_settings_version_is_written_only_when_it_changes(conn: Connection) -> None:
    store = SqlBrandStore(conn)
    ola = store.brand(NewBrand(add_user(conn), "Ola", "ola", NOW))
    twelve_hours = Schedule(720)
    assert store.set_schedule(ola, twelve_hours, at=NOW)
    assert not store.set_schedule(ola, twelve_hours, at=NOW + timedelta(1))
    quiet = Schedule(720, quiet_start=time(0), quiet_end=time(6))
    assert store.set_schedule(ola, quiet, at=NOW + timedelta(2))
    assert not store.set_schedule(ola, quiet, at=NOW + timedelta(3))  # times compare too
    assert count(conn, "brand_schedule_versions") == 2
    assert store.set_schedule(ola, None, at=NOW + timedelta(4))  # scanned on request only
    assert not store.set_schedule(ola, None, at=NOW + timedelta(5))
    latest = select(table("brand_schedule_versions").c.interval_minutes).order_by(
        table("brand_schedule_versions").c.created_at.desc()
    )
    assert conn.execute(latest.limit(1)).scalar_one() is None
    lean = {"languages": ["en"], "maps": {"enabled": False}}
    assert store.set_search_settings(ola, lean, at=NOW)
    assert not store.set_search_settings(ola, dict(lean), at=NOW + timedelta(1))
    assert store.set_search_settings(
        ola, {**lean, "news": {"enabled": False}}, at=NOW + timedelta(2)
    )
    as_tuples = MappingProxyType({"languages": ("en",), "maps": {"enabled": False}})
    assert store.set_search_settings(ola, as_tuples, at=NOW + timedelta(3))  # back to lean
    assert not store.set_search_settings(ola, lean, at=NOW + timedelta(4))  # tuples are lists
    assert count(conn, "brand_search_settings_versions") == 3
    with pytest.raises(DBAPIError):  # never two versions at one instant
        store.set_search_settings(ola, {"news": {"enabled": True}}, at=NOW + timedelta(3))


def test_a_new_brand_is_checked_before_it_reaches_the_database() -> None:
    owner = uuid.uuid4()
    for name, slug in (("  ", "ola"), ("x" * 121, "ola"), ("Ola", "Ola"), ("Ola", "ola--cabs")):
        with pytest.raises(ValueError):
            NewBrand(owner, name, slug, NOW)
