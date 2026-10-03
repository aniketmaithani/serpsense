"""The mention store on real Postgres: first-seen mentions, revisions and observations."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection, select

from serpsense.adapters.db.mention_store import SqlMentionStore
from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import ParsedMention, url_key
from serpsense.ports.mention_store import MentionStore, Recorded, Sighting
from tests.integration.db_helpers import (
    NOW,
    add_app,
    add_location,
    add_owned_brand,
    add_scan,
    table,
)

pytestmark = pytest.mark.integration

MENTIONS, REVISIONS = table("mentions"), table("mention_revisions")
OBSERVATIONS = table("mention_observations")
A, B = "https://news.example.in/ola-fares", "https://news.example.in/ola-ipo"


@pytest.fixture
def store(conn: Connection) -> MentionStore:
    return SqlMentionStore(conn)


def news(url: str, position: int | None, text: str = "Ola revises fares") -> ParsedMention:
    return ParsedMention(MentionSource.NEWS, url_key(url), text, url=url, position=position)


def review(text: str, stars: int) -> ParsedMention:
    return ParsedMention(
        MentionSource.PLAY_REVIEW, "gp:AOqpTO1", text, position=1, star_rating=stars
    )


def scans(conn: Connection, brand_id: uuid.UUID, count: int) -> list[uuid.UUID]:
    """Finished scans 12 hours apart, so none is active."""
    return [
        add_scan(conn, brand_id, scheduled_for=NOW + timedelta(hours=12 * n), status="succeeded")
        for n in range(count)
    ]


def test_a_first_sighting_stores_each_mention_once_at_its_best_rank(
    conn: Connection, store: MentionStore
) -> None:
    (scan,) = scans(conn, add_owned_brand(conn), 1)
    seen = (news(A, 3), news(B, None), news(A, 1, "Ola revises fares again"))
    assert store.record(Sighting(scan, seen), at=NOW) == Recorded(new=2, revised=0, observed=2)
    rows = conn.execute(
        select(MENTIONS.c.url, MENTIONS.c.text, OBSERVATIONS.c.position)
        .join(OBSERVATIONS, OBSERVATIONS.c.mention_id == MENTIONS.c.id)
        .order_by(MENTIONS.c.url)
    ).all()
    assert [tuple(row) for row in rows] == [
        (A, "Ola revises fares again", 1),
        (B, "Ola revises fares", None),
    ]


def test_a_later_scan_only_observes_and_a_retry_changes_nothing(
    conn: Connection, store: MentionStore
) -> None:
    first, second = scans(conn, add_owned_brand(conn), 2)
    store.record(Sighting(first, (news(A, 2),)), at=NOW)
    later = Sighting(second, (news(A, 1, "Ola fares: a snippet varies between queries"),))
    assert store.record(later, at=NOW + timedelta(hours=12)) == Recorded(
        new=0, revised=0, observed=1
    )
    assert store.record(later, at=NOW + timedelta(hours=12)) == Recorded(
        new=0, revised=0, observed=0
    )
    assert conn.execute(select(MENTIONS.c.text)).scalar_one() == "Ola revises fares"  # first-seen
    positions = select(OBSERVATIONS.c.position).order_by(OBSERVATIONS.c.position.desc())
    assert conn.execute(positions).scalars().all() == [2, 1]
    assert conn.execute(select(REVISIONS.c.revision)).all() == []  # only reviews are edited


def test_an_edited_review_keeps_its_history(conn: Connection, store: MentionStore) -> None:
    brand_id = add_owned_brand(conn)
    app_id = add_app(conn, brand_id)
    texts = ["Driver was late", "driver  WAS late", "Driver cancelled twice", "Driver was late"]
    seen = [
        Sighting(scan, (review(text, stars),), brand_app_id=app_id)
        for scan, text, stars in zip(scans(conn, brand_id, 4), texts, (1, 2, 3, 4), strict=True)
    ]
    revised = [
        store.record(s, at=NOW + timedelta(hours=12 * n)).revised for n, s in enumerate(seen)
    ]
    assert revised == [0, 0, 1, 1]  # case and spacing aren't an edit; A → B → A is revision 3
    assert store.record(seen[3], at=NOW + timedelta(days=2)) == Recorded(
        new=0, revised=0, observed=0
    )
    history = select(REVISIONS.c.revision, REVISIONS.c.text, REVISIONS.c.scan_id)
    assert [tuple(row) for row in conn.execute(history.order_by(REVISIONS.c.revision))] == [
        (2, "Driver cancelled twice", seen[2].scan_id),
        (3, "Driver was late", seen[3].scan_id),
    ]
    assert conn.execute(select(MENTIONS.c.brand_app_id)).scalar_one() == app_id
    stars = select(OBSERVATIONS.c.star_rating).order_by(OBSERVATIONS.c.star_rating)
    assert conn.execute(stars).scalars().all() == [1, 2, 3, 4]


def test_each_brand_has_its_own_mentions(conn: Connection, store: MentionStore) -> None:
    for slug in ("ola", "uber"):
        (scan,) = scans(conn, add_owned_brand(conn, slug), 1)
        assert store.record(Sighting(scan, (news(A, 1),)), at=NOW).new == 1
    assert len(conn.execute(select(MENTIONS.c.id)).all()) == 2


def test_an_empty_sighting_writes_nothing(store: MentionStore) -> None:
    assert store.record(Sighting(uuid.uuid4(), ()), at=NOW) == Recorded(
        new=0, revised=0, observed=0
    )


def test_each_review_cites_only_its_own_app_or_place(conn: Connection, store: MentionStore) -> None:
    brand_id = add_owned_brand(conn)
    app_id, place_id = add_app(conn, brand_id), add_location(conn, brand_id)
    first, second = scans(conn, brand_id, 2)
    maps = ParsedMention(MentionSource.MAPS_REVIEW, "maps:1", "Rude staff", star_rating=2)
    seen = (news(A, 1), review("Late again", 1), maps)
    cited = {"brand_app_id": app_id, "brand_location_id": place_id}
    assert store.record(Sighting(first, seen, **cited), at=NOW).new == 3
    rows = select(MENTIONS.c.source, MENTIONS.c.brand_app_id, MENTIONS.c.brand_location_id)
    assert {tuple(row) for row in conn.execute(rows)} == {
        (MentionSource.NEWS, None, None),
        (MentionSource.PLAY_REVIEW, app_id, None),
        (MentionSource.MAPS_REVIEW, None, place_id),
    }
    edited = ParsedMention(MentionSource.MAPS_REVIEW, "maps:1", "Rude staff, no refund")
    later = Sighting(second, (edited,), brand_location_id=place_id)
    assert store.record(later, at=NOW + timedelta(hours=12)).revised == 1


def test_a_naive_time_is_refused(store: MentionStore) -> None:
    with pytest.raises(ValueError, match="timezone"):
        store.record(Sighting(uuid.uuid4(), ()), at=NOW.replace(tzinfo=None))
