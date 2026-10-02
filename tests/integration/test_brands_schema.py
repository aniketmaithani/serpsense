"""Brand and competitor rules enforced by Postgres itself (data-model.md §3)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import add_brand, add_user, table, violation

pytestmark = pytest.mark.integration

USERS = table("users")
BRANDS = table("brands")
COMPETITORS = table("brand_competitors")


def link(conn: Connection, brand_id: uuid.UUID, competitor_id: uuid.UUID) -> None:
    conn.execute(insert(COMPETITORS).values(brand_id=brand_id, competitor_brand_id=competitor_id))


def test_brand_round_trips(conn: Connection) -> None:
    brand_id = add_brand(conn, add_user(conn), slug="voltbox-india", tone_notes="Warm, direct.")
    row = conn.execute(select(BRANDS).where(BRANDS.c.id == brand_id)).one()
    assert (row.name, row.slug, row.tone_notes, row.archived_at) == (
        "VoltBox",
        "voltbox-india",
        "Warm, direct.",
        None,
    )


def test_slug_is_unique_per_owner_only(conn: Connection) -> None:
    owner = add_user(conn)
    add_brand(conn, owner, slug="voltbox")
    add_brand(conn, add_user(conn, "other@example.com"), slug="voltbox")
    with pytest.raises(IntegrityError) as exc:
        add_brand(conn, owner, slug="voltbox")
    assert violation(exc).constraint_name == "uq_brands_owner_id_slug"


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"name": "   "}, "ck_brands_name_length"),
        ({"name": "x" * 121}, "ck_brands_name_length"),
        ({"slug": "Volt Box"}, "ck_brands_slug_format"),
        ({"slug": "volt--box"}, "ck_brands_slug_format"),
        ({"slug": "-voltbox"}, "ck_brands_slug_format"),
        ({"slug": "a" * 65}, "ck_brands_slug_format"),
        ({"tone_notes": "x" * 2001}, "ck_brands_tone_notes_length"),
    ],
)
def test_brand_checks(conn: Connection, overrides: dict[str, Any], constraint: str) -> None:
    values = {"slug": "voltbox", **overrides}
    with pytest.raises(IntegrityError) as exc:
        add_brand(conn, add_user(conn), **values)
    assert violation(exc).constraint_name == constraint


def test_brand_accepts_limits(conn: Connection) -> None:
    add_brand(conn, add_user(conn), name="x" * 120, slug="a" * 64, tone_notes="x" * 2000)


def test_brand_owner_cannot_change(conn: Connection) -> None:
    brand_id = add_brand(conn, add_user(conn))
    other = add_user(conn, "other@example.com")
    conn.execute(update(BRANDS).where(BRANDS.c.id == brand_id).values(name="VoltBox India"))
    with pytest.raises(IntegrityError) as exc:
        conn.execute(update(BRANDS).where(BRANDS.c.id == brand_id).values(owner_id=other))
    assert violation(exc).constraint_name == "ck_brands_owner_immutable"


def test_competitor_link_round_trips(conn: Connection) -> None:
    owner = add_user(conn)
    brand, rival = add_brand(conn, owner, slug="voltbox"), add_brand(conn, owner, slug="soundnest")
    link(conn, brand, rival)
    rivals = conn.execute(
        select(COMPETITORS.c.competitor_brand_id).where(COMPETITORS.c.brand_id == brand)
    ).scalars()
    assert list(rivals) == [rival]


def test_brand_cannot_compete_with_itself(conn: Connection) -> None:
    brand = add_brand(conn, add_user(conn))
    with pytest.raises(IntegrityError) as exc:
        link(conn, brand, brand)
    assert violation(exc).constraint_name == "ck_brand_competitors_not_self"


def test_competitor_must_share_owner_on_insert(conn: Connection) -> None:
    brand = add_brand(conn, add_user(conn))
    stranger = add_brand(conn, add_user(conn, "other@example.com"), slug="soundnest")
    with pytest.raises(IntegrityError) as exc:
        link(conn, brand, stranger)
    assert violation(exc).constraint_name == "ck_brand_competitors_same_owner"


def test_competitor_must_share_owner_on_update(conn: Connection) -> None:
    owner = add_user(conn)
    brand, rival = add_brand(conn, owner, slug="voltbox"), add_brand(conn, owner, slug="soundnest")
    stranger = add_brand(conn, add_user(conn, "other@example.com"), slug="boat")
    link(conn, brand, rival)
    with pytest.raises(IntegrityError) as exc:
        conn.execute(
            update(COMPETITORS)
            .where(COMPETITORS.c.brand_id == brand)
            .values(competitor_brand_id=stranger)
        )
    assert violation(exc).constraint_name == "ck_brand_competitors_same_owner"


@pytest.mark.parametrize(
    ("target", "constraint"),
    [
        ("competitor", "fk_brand_competitors_competitor_brand_id_brands"),
        ("brand", "fk_brand_competitors_brand_id_brands"),
        ("owner", "fk_brands_owner_id_users"),
    ],
)
def test_referenced_rows_cannot_be_deleted(conn: Connection, target: str, constraint: str) -> None:
    owner = add_user(conn)
    brand, rival = add_brand(conn, owner, slug="voltbox"), add_brand(conn, owner, slug="soundnest")
    link(conn, brand, rival)
    statement = {
        "competitor": delete(BRANDS).where(BRANDS.c.id == rival),
        "brand": delete(BRANDS).where(BRANDS.c.id == brand),
        "owner": delete(USERS).where(USERS.c.id == owner),
    }[target]
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statement)
    assert violation(exc).constraint_name == constraint
