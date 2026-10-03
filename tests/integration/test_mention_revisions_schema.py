"""Mention revisions: edited reviews keep their history (data-model §5, owner decision)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add_app,
    add_mention,
    add_owned_brand,
    add_scan,
    table,
    violation,
)

pytestmark = pytest.mark.integration

REVISIONS = table("mention_revisions")


SCANS = table("scans")


def review(conn: Connection, brand_id: uuid.UUID, key: str = "r1") -> uuid.UUID:
    app_id = add_app(conn, brand_id, app_id=f"com.ola.{key}")
    no_link = {"url": None, "outlet": None}
    return add_mention(
        conn, brand_id, source="play_review", identity_key=key, brand_app_id=app_id, **no_link
    )


def setup(conn: Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A brand, one of its reviews and a scan of it."""
    brand_id = add_owned_brand(conn)
    return brand_id, review(conn, brand_id), add_scan(conn, brand_id)


def next_scan(conn: Connection, brand_id: uuid.UUID, previous: uuid.UUID) -> uuid.UUID:
    """The brand's next scan; the previous one finishes first (one active scan per brand)."""
    conn.execute(update(SCANS).where(SCANS.c.id == previous).values(status="succeeded"))
    return add_scan(conn, brand_id, scheduled_for=NOW + timedelta(hours=6))


def revise(conn: Connection, mention_id: uuid.UUID, scan_id: uuid.UUID, **overrides: Any) -> None:
    values = {"revision": 2, "text": "Edited: driver was polite", "created_at": NOW, **overrides}
    conn.execute(REVISIONS.insert().values(mention_id=mention_id, scan_id=scan_id, **values))


def test_a_revision_is_kept_and_its_text_can_be_scrubbed(conn: Connection) -> None:
    _, mention_id, scan_id = setup(conn)
    revise(conn, mention_id, scan_id)
    conn.execute(update(REVISIONS).values(text="[removed]"))  # a redaction scrub
    assert conn.execute(select(REVISIONS.c.text)).scalar_one() == "[removed]"


def test_only_reviews_have_revisions(conn: Connection) -> None:
    brand_id = add_owned_brand(conn)
    news = add_mention(conn, brand_id)  # a search snippet varies by query: not an edit
    with pytest.raises(IntegrityError) as exc:
        revise(conn, news, add_scan(conn, brand_id))
    assert violation(exc).constraint_name == "ck_mention_revisions_review_only"


@pytest.mark.parametrize("mutation", ["delete", "truncate"])
def test_revisions_are_never_deleted(conn: Connection, mutation: str) -> None:
    _, mention_id, scan_id = setup(conn)
    revise(conn, mention_id, scan_id)
    statements: dict[str, Executable] = {
        "delete": delete(REVISIONS),
        "truncate": text("TRUNCATE mention_revisions"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"revision": 1}, "ck_mention_revisions_revision_after_first"),
        ({"text": " \n"}, "ck_mention_revisions_text_length"),
        ({"text": "x" * 10_001}, "ck_mention_revisions_text_length"),
    ],
)
def test_revision_checks(conn: Connection, overrides: dict[str, Any], constraint: str) -> None:
    _, mention_id, scan_id = setup(conn)
    with pytest.raises(IntegrityError) as exc:
        revise(conn, mention_id, scan_id, **overrides)
    assert violation(exc).constraint_name == constraint


def test_one_new_text_per_mention_per_scan_and_per_number(conn: Connection) -> None:
    brand_id, mention_id, scan_id = setup(conn)
    revise(conn, mention_id, scan_id)
    with pytest.raises(IntegrityError) as exc, conn.begin_nested():
        revise(conn, mention_id, scan_id, revision=3)
    assert violation(exc).constraint_name == "uq_mention_revisions_mention_id_scan_id"
    later = next_scan(conn, brand_id, scan_id)
    with pytest.raises(IntegrityError) as exc, conn.begin_nested():
        revise(conn, mention_id, later)  # revision 2 again
    assert violation(exc).constraint_name == "pk_mention_revisions"
    revise(conn, mention_id, later, revision=3)


def test_the_scan_must_be_of_the_mentions_brand(conn: Connection) -> None:
    _, mention_id, _ = setup(conn)
    other_scan = add_scan(conn, add_owned_brand(conn, "rapido"))
    with pytest.raises(IntegrityError) as exc:
        revise(conn, mention_id, other_scan)
    assert violation(exc).constraint_name == "ck_mention_revisions_same_brand"


@pytest.mark.parametrize("column", ["mention_id", "revision", "scan_id", "created_at"])
def test_identity_cannot_change(conn: Connection, column: str) -> None:
    brand_id, mention_id, scan_id = setup(conn)
    revise(conn, mention_id, scan_id)
    changed = {
        "mention_id": review(conn, brand_id, "r2"),
        "revision": 3,
        "scan_id": next_scan(conn, brand_id, scan_id),
        "created_at": NOW + timedelta(days=1),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(update(REVISIONS).values({column: changed[column]}))
    assert violation(exc).constraint_name == "ck_mention_revisions_identity_immutable"


@pytest.mark.parametrize("target", ["mentions", "scans"])
def test_revised_mention_and_its_scan_cannot_be_deleted(conn: Connection, target: str) -> None:
    _, mention_id, scan_id = setup(conn)
    revise(conn, mention_id, scan_id)
    victim = mention_id if target == "mentions" else scan_id
    column = "mention_id" if target == "mentions" else "scan_id"
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table(target)).where(table(target).c.id == victim))
    assert violation(exc).constraint_name == f"fk_mention_revisions_{column}_{target}"
