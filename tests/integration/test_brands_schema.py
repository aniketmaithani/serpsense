"""Brand and competitor rules enforced by Postgres itself (data-model.md §3)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, delete, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, OperationalError

from tests.integration.db_helpers import NOW, add_brand, add_user, table, violation

pytestmark = pytest.mark.integration

NOT_NULL_VIOLATION = "23502"
LOCK_NOT_AVAILABLE = "55P03"

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
        ({"name": "\t\n"}, "ck_brands_name_length"),
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
    values = {"name": "x" * 120, "slug": "a" * 64, "tone_notes": "x" * 2000}
    brand_id = add_brand(conn, add_user(conn), **values)
    row = conn.execute(select(BRANDS).where(BRANDS.c.id == brand_id)).one()
    assert (row.name, row.slug, row.tone_notes) == tuple(values.values())


def test_account_deletion_cleanup_is_allowed(conn: Connection) -> None:
    """ADR-0013: archive the brand and drop tone notes; re-setting the same owner is fine."""
    owner = add_user(conn)
    brand_id = add_brand(conn, owner, tone_notes="Mentions the founder by name.")
    conn.execute(
        update(BRANDS)
        .where(BRANDS.c.id == brand_id)
        .values(archived_at=NOW, tone_notes=None, owner_id=owner)
    )
    row = conn.execute(select(BRANDS).where(BRANDS.c.id == brand_id)).one()
    assert (row.archived_at, row.tone_notes, row.owner_id) == (NOW, None, owner)


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


def test_missing_competitor_reports_the_foreign_key(conn: Connection) -> None:
    with pytest.raises(IntegrityError) as exc:
        link(conn, add_brand(conn, add_user(conn)), uuid.uuid4())
    assert violation(exc).constraint_name == "fk_brand_competitors_competitor_brand_id_brands"


def test_missing_competitor_id_reports_not_null(conn: Connection) -> None:
    brand = add_brand(conn, add_user(conn))
    with pytest.raises(IntegrityError) as exc:
        conn.execute(insert(COMPETITORS).values(brand_id=brand, competitor_brand_id=None))
    assert violation(exc).sqlstate == NOT_NULL_VIOLATION
    assert violation(exc).column_name == "competitor_brand_id"


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


def test_a_brand_has_at_most_four_competitors(conn: Connection) -> None:
    owner = add_user(conn)
    brand = add_brand(conn, owner, slug="ola")
    rivals = [add_brand(conn, owner, slug=f"rival-{n}") for n in range(5)]
    link(conn, rivals[0], brand)  # a link to the brand isn't one of its competitors
    for rival in rivals[:4]:
        link(conn, brand, rival)
    with pytest.raises(IntegrityError) as exc, conn.begin_nested():
        link(conn, brand, rivals[4])
    assert violation(exc).constraint_name == "ck_brand_competitors_at_most_four"
    again = pg_insert(COMPETITORS).values(brand_id=brand, competitor_brand_id=rivals[0])
    conn.execute(again.on_conflict_do_nothing())  # re-linking an existing pair is a no-op


def test_a_full_brand_can_swap_a_competitor_but_not_receive_one(conn: Connection) -> None:
    owner = add_user(conn)
    full, other = add_brand(conn, owner, slug="ola"), add_brand(conn, owner, slug="uber")
    rivals = [add_brand(conn, owner, slug=f"rival-{n}") for n in range(5)]
    for rival in rivals[:4]:
        link(conn, full, rival)
    swap = update(COMPETITORS).where(COMPETITORS.c.competitor_brand_id == rivals[0])
    conn.execute(swap.values(brand_id=full, competitor_brand_id=rivals[4]))  # still four
    link(conn, other, rivals[0])
    with pytest.raises(IntegrityError) as exc:
        conn.execute(
            update(COMPETITORS).where(COMPETITORS.c.brand_id == other).values(brand_id=full)
        )
    assert violation(exc).constraint_name == "ck_brand_competitors_at_most_four"


def test_concurrent_links_cannot_pass_four(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as setup:
        owner = add_user(setup, f"{uuid.uuid4().hex}@example.com")
        brand = add_brand(setup, owner, slug="ola")
        rivals = [add_brand(setup, owner, slug=f"rival-{n}") for n in range(5)]
        for rival in rivals[:3]:
            link(setup, brand, rival)
    try:
        with migrated_engine.connect() as first, migrated_engine.connect() as second:
            first_tx = first.begin()
            link(first, brand, rivals[3])  # the fourth, not yet committed
            second_tx = second.begin()
            second.execute(text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(OperationalError) as blocked:
                link(second, brand, rivals[4])
            assert violation(blocked).sqlstate == LOCK_NOT_AVAILABLE  # waits on the brand row
            second_tx.rollback()
            first_tx.commit()
            retry_tx = second.begin()
            with pytest.raises(IntegrityError) as exc:
                link(second, brand, rivals[4])
            assert violation(exc).constraint_name == "ck_brand_competitors_at_most_four"
            retry_tx.rollback()
    finally:
        with migrated_engine.begin() as cleanup:
            cleanup.execute(delete(COMPETITORS).where(COMPETITORS.c.brand_id == brand))
            cleanup.execute(delete(BRANDS).where(BRANDS.c.id.in_([brand, *rivals])))
            cleanup.execute(delete(USERS).where(USERS.c.id == owner))
