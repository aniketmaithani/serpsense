"""The brand behind a scan on real Postgres."""

import uuid

import pytest
from sqlalchemy import Connection, insert, update

from serpsense.adapters.db.scan_targets import SqlScanTargets
from serpsense.ports.scan_targets import NamedBrand, StoreApp
from tests.integration.db_helpers import NOW, add, add_app, add_brand, add_scan, add_user, table

pytestmark = pytest.mark.integration


def link(conn: Connection, brand_id: uuid.UUID, competitor_id: uuid.UUID) -> None:
    rows = {"brand_id": brand_id, "competitor_brand_id": competitor_id}
    conn.execute(insert(table("brand_competitors")).values(**rows))


def test_a_scan_target_is_its_brand_with_competitors_apps_and_aliases(conn: Connection) -> None:
    owner = add_user(conn)
    ola = add_brand(conn, owner, slug="ola", name="Ola")
    uber = add_brand(conn, owner, slug="uber", name="Uber")
    rapido = add_brand(conn, owner, slug="rapido", name="Rapido")
    for rival in (uber, rapido):
        link(conn, ola, rival)
    link(conn, uber, ola)  # a link the other way is Uber's, not Ola's
    pay = add_app(conn, ola, app_id="com.olacabs.olamoney")
    cabs = add_app(conn, ola, app_id="com.olacabs.customer")
    add_app(conn, uber, app_id="com.ubercab")
    for alias in ("OLA Money", "ola cabs"):
        add(conn, table("brand_aliases"), brand_id=ola, alias=alias)
    add(conn, table("brand_aliases"), brand_id=uber, alias="Uber Eats")
    scan_id = add_scan(conn, ola, settings_snapshot={"languages": ["en"]})

    target = SqlScanTargets(conn).for_scan(scan_id)
    assert target is not None
    assert (target.scan_id, target.brand, target.owner_id) == (
        scan_id,
        NamedBrand(ola, "Ola"),
        owner,
    )
    assert target.settings_snapshot == {"languages": ["en"]}
    assert target.aliases == ("ola cabs", "OLA Money")  # case-insensitive order, as citext
    assert target.competitors == (NamedBrand(rapido, "Rapido"), NamedBrand(uber, "Uber"))
    assert target.apps == (
        StoreApp(cabs, "com.olacabs.customer"),
        StoreApp(pay, "com.olacabs.olamoney"),
    )
    assert (target.brand_archived, target.owner_deleted) == (False, False)


@pytest.mark.parametrize(
    ("archived", "deleted"), [(True, False), (False, True)], ids=["archived", "owner-deleted"]
)
def test_an_archived_brand_or_a_deleted_owner_is_reported(
    conn: Connection, archived: bool, deleted: bool
) -> None:
    owner = add_user(conn)
    brand_id = add_brand(conn, owner)
    scan_id = add_scan(conn, brand_id)
    brands, users = table("brands"), table("users")
    if archived:
        conn.execute(update(brands).where(brands.c.id == brand_id).values(archived_at=NOW))
    if deleted:
        conn.execute(update(users).where(users.c.id == owner).values(deleted_at=NOW))
    target = SqlScanTargets(conn).for_scan(scan_id)
    assert target is not None and (target.brand_archived, target.owner_deleted) == (
        archived,
        deleted,
    )
    assert (target.competitors, target.apps, target.aliases) == ((), (), ())


def test_an_unknown_scan_has_no_target(conn: Connection) -> None:
    assert SqlScanTargets(conn).for_scan(uuid.uuid4()) is None
