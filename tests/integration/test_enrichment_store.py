"""The enrichment store on real Postgres: what still needs labelling, and the labels."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection, insert, select

from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.domain.enums import LlmTask, MentionSource, Topic
from serpsense.domain.labelling import sources_for
from serpsense.ports.enrichment_store import EnrichmentStore, MentionLabel, PendingText
from tests.integration.db_helpers import (
    NOW,
    add_app,
    add_brand,
    add_llm_call,
    add_mention,
    add_scan,
    add_user,
    table,
)

pytestmark = pytest.mark.integration

PROMPT = "label_mentions/v1"
LABELS = sources_for(LlmTask.LABEL_MENTIONS)
WEEK = timedelta(days=7)


@pytest.fixture
def store(conn: Connection) -> EnrichmentStore:
    return SqlEnrichmentStore(conn)


def observe(conn: Connection, mention_id: uuid.UUID, scan_id: uuid.UUID) -> None:
    conn.execute(
        insert(table("mention_observations")).values(mention_id=mention_id, scan_id=scan_id)
    )


def pending(
    store: EnrichmentStore, brand_id: uuid.UUID, prompt: str = PROMPT, limit: int = 10
) -> list[PendingText]:
    found = store.pending(
        brand_id, prompt_version=prompt, sources=LABELS, seen_since=NOW - WEEK, limit=limit
    )
    return list(found)


def label(mention_id: uuid.UUID, revision: int = 1) -> MentionLabel:
    return MentionLabel(mention_id, revision, -1, 30, Topic.RELIABILITY, True, True, "Late driver.")


def test_pending_texts_are_the_latest_unlabelled_ones_seen_lately(
    conn: Connection, store: EnrichmentStore
) -> None:
    owner = add_user(conn)
    brand_id = add_brand(conn, owner)
    recent = add_scan(conn, brand_id, status="succeeded")
    two_weeks_ago = {"scheduled_for": NOW - 2 * WEEK, "created_at": NOW - 2 * WEEK}
    old = add_scan(conn, brand_id, status="succeeded", **two_weeks_ago)
    news = add_mention(conn, brand_id)
    play = {"source": "play_review", "identity_key": "gp:1", "url": None, "outlet": None}
    review = add_mention(conn, brand_id, text="Late", brand_app_id=add_app(conn, brand_id), **play)
    edited = {"mention_id": review, "revision": 2, "text": "Late again", "scan_id": recent}
    conn.execute(insert(table("mention_revisions")).values(created_at=NOW, **edited))
    stale = add_mention(conn, brand_id, identity_key="f" * 64, url="https://x.in/old")
    for mention, scan in ((news, recent), (review, recent), (stale, old)):
        observe(conn, mention, scan)
    suggestion = {"source": "autocomplete", "identity_key": "a" * 64, "url": None, "outlet": None}
    observe(conn, add_mention(conn, brand_id, text="ola cancel fee", **suggestion), recent)
    other = add_brand(conn, owner, slug="uber")
    observe(conn, add_mention(conn, other), add_scan(conn, other, status="succeeded"))

    waiting = pending(store, brand_id)  # the autocomplete suggestion belongs to another task
    assert {(p.mention_id, p.revision, p.text) for p in waiting} == {
        (news, 1, "VoltBox earbuds recalled after battery complaints"),
        (review, 2, "Late again"),  # the latest revision, not the mention's first text
    }
    assert {p.source for p in waiting} == {MentionSource.NEWS, MentionSource.PLAY_REVIEW}
    assert len(pending(store, brand_id, limit=1)) == 1

    call_id = add_llm_call(conn, owner)
    assert store.record([label(review, 2)], prompt_version=PROMPT, llm_call_id=call_id, at=NOW) == 1
    left = pending(store, brand_id)
    assert [p.mention_id for p in left] == [news]  # labelled under this prompt: no longer pending
    assert len(pending(store, brand_id, "label_mentions/v2")) == 2  # a new version labels again


def test_labels_are_written_once(conn: Connection, store: EnrichmentStore) -> None:
    owner = add_user(conn)
    brand_id = add_brand(conn, owner)
    mention_id = add_mention(conn, brand_id)
    call_id = add_llm_call(conn, owner)
    assert (
        store.record([label(mention_id)], prompt_version=PROMPT, llm_call_id=call_id, at=NOW) == 1
    )
    again = label(mention_id)
    assert store.record([again], prompt_version=PROMPT, llm_call_id=call_id, at=NOW) == 0
    assert store.record([], prompt_version=PROMPT, llm_call_id=call_id, at=NOW) == 0
    rows = conn.execute(select(table("enrichments").c.topic, table("enrichments").c.llm_call_id))
    assert [tuple(row) for row in rows] == [("reliability", call_id)]


def test_a_pending_text_is_a_plain_value() -> None:
    text = PendingText(uuid.uuid4(), 1, MentionSource.NEWS, "en", "Ola fares up")
    assert text.revision == 1
