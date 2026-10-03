"""The narrative store on real Postgres: the mentions waiting for a story, and when a brand's
mentions were last offered to the model."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, insert

from serpsense.adapters.db.narrative_store import SqlNarrativeStore
from serpsense.domain.enums import LlmTask, MentionSource, Topic
from serpsense.ports.narrative_store import NarrativeStore, UngroupedMention
from tests.integration.db_helpers import (
    NOW,
    add,
    add_app,
    add_brand,
    add_llm_call,
    add_mention,
    add_scan,
    add_user,
    table,
)
from tests.integration.test_narratives_schema import GROUPING, assign, narrative

pytestmark = pytest.mark.integration

WEEK, HOUR = timedelta(days=7), timedelta(hours=1)
ACTIVE = {LlmTask.LABEL_MENTIONS: "label_mentions/v1"}
NEGATIVE = {"sentiment": -1, "is_complaint": True, "is_about_brand": True}


@pytest.fixture
def store(conn: Connection) -> NarrativeStore:
    return SqlNarrativeStore(conn)


class Brand:
    """An owner's brand, with a labelling call and mentions first seen now."""

    def __init__(self, conn: Connection, slug: str = "voltbox") -> None:
        self.conn = conn
        self.owner = add_user(conn, f"{slug}@example.com")
        self.id = add_brand(conn, self.owner, slug=slug)
        self.labelling = add_llm_call(conn, self.owner)
        self.app = add_app(conn, self.id)
        self.scan = add_scan(conn, self.id, status="succeeded")
        self.made = 0

    def mention(self, label: dict[str, Any] | None = None, **overrides: Any) -> uuid.UUID:
        """A news mention, labelled (negative by default)."""
        self.made += 1
        url, key = f"https://news.example.in/{self.made}", f"{self.made:064x}"
        values = {"url": url, "identity_key": key, "created_at": NOW + timedelta(minutes=self.made)}
        mention_id = add_mention(self.conn, self.id, **{**values, **overrides})
        self.label(mention_id, **(NEGATIVE if label is None else label))
        return mention_id

    def edited_review(self, text: str) -> uuid.UUID:
        """A Play review, labelled negative, whose author has since rewritten it."""
        review = {"source": "play_review", "url": None, "outlet": None}
        mention_id = self.mention(identity_key=f"gp:{self.made}", **review, brand_app_id=self.app)
        revision = {"mention_id": mention_id, "revision": 2, "text": text, "scan_id": self.scan}
        self.conn.execute(insert(table("mention_revisions")).values(created_at=NOW, **revision))
        return mention_id

    def label(self, mention_id: uuid.UUID, **values: Any) -> None:
        row = {"revision": 1, "prompt_version": "label_mentions/v1", "llm_call_id": self.labelling}
        defaults = {"severity": 40, "topic": "pricing", "reason": "A fare.", "created_at": NOW}
        add(self.conn, table("enrichments"), mention_id=mention_id, **{**row, **defaults, **values})

    def waiting(self, store: NarrativeStore, **kw: Any) -> list[UngroupedMention]:
        since, prompts, limit = NOW - WEEK, kw.pop("prompts", ACTIVE), kw.pop("limit", 10)
        return list(store.ungrouped(self.id, first_seen_since=since, prompts=prompts, limit=limit))


def test_mentions_wait_for_a_story_when_unfavourable_recent_and_in_none(
    conn: Connection, store: NarrativeStore
) -> None:
    brand = Brand(conn)
    negative = brand.mention()
    complaint = brand.mention({**NEGATIVE, "sentiment": 0})  # a complaint, however worded
    brand.mention({**NEGATIVE, "sentiment": 1, "is_complaint": False})
    brand.mention({**NEGATIVE, "is_about_brand": False})  # only shares the name
    brand.mention(created_at=NOW - 2 * WEEK)  # first seen long ago, though still seen
    better = brand.edited_review("Fixed now, thanks")
    brand.label(better, revision=2, sentiment=1, is_complaint=False, is_about_brand=True)
    worse = brand.edited_review("Charged twice, no refund")
    brand.label(worse, revision=2, **NEGATIVE, topic="billing_refunds", created_at=NOW + HOUR)
    brand.edited_review("Worse now")  # its new text has no label yet
    placed = brand.mention()
    call = add_llm_call(conn, brand.owner, **GROUPING)
    assign(conn, narrative(conn, brand.id, call), placed, call)
    Brand(conn, "rival").mention()

    waiting = brand.waiting(store)
    assert [m.mention_id for m in waiting] == [worse, complaint, negative]  # newest first
    review, *_, first = waiting
    got = (review.source, review.text, review.topic, review.labelled_at)
    assert got == (
        MentionSource.PLAY_REVIEW,
        "Charged twice, no refund",
        Topic.BILLING_REFUNDS,
        NOW + HOUR,
    )
    got = (first.source, first.language_code, first.topic, first.severity, first.labelled_at)
    assert got == (MentionSource.NEWS, "en", Topic.PRICING, 40, NOW)
    assert first.text == "VoltBox earbuds recalled after battery complaints"
    assert len(brand.waiting(store, limit=1)) == 1


def test_the_label_that_counts_is_the_active_prompts_then_the_newest(
    conn: Connection, store: NarrativeStore
) -> None:
    brand = Brand(conn)
    relabelled = brand.mention()  # negative under v1, then neutral under v2
    v2 = add_llm_call(conn, brand.owner, prompt_version="label_mentions/v2")
    newer = {"prompt_version": "label_mentions/v2", "llm_call_id": v2, "created_at": NOW + WEEK}
    brand.label(relabelled, sentiment=0, is_complaint=False, is_about_brand=True, **newer)
    assert [m.mention_id for m in brand.waiting(store)] == [relabelled]  # v1 is active
    assert brand.waiting(store, prompts={LlmTask.LABEL_MENTIONS: "label_mentions/v2"}) == []
    assert brand.waiting(store, prompts={}) == []  # no active prompt: the newest label


def test_a_brands_mentions_count_as_considered_after_its_last_successful_grouping(
    conn: Connection, store: NarrativeStore
) -> None:
    brand, rival = Brand(conn), Brand(conn, "rival")
    assert store.considered_until(brand.id) is None  # never grouped
    scan, theirs = brand.scan, rival.scan

    def call(at: timedelta, **values: Any) -> None:
        add_llm_call(conn, brand.owner, created_at=NOW + at, **{**GROUPING, **values})

    call(HOUR, scan_id=scan)
    call(2 * HOUR, scan_id=None)  # an eval or a draft: in no scan
    call(3 * HOUR, scan_id=scan, task="label_mentions", prompt_version="label_mentions/v1")
    add_llm_call(conn, rival.owner, created_at=NOW + 4 * HOUR, scan_id=theirs, **GROUPING)
    assert store.considered_until(brand.id) == NOW + HOUR
    failed = {"outcome": "failed", "served_model": None, "stop_reason": None, "input_tokens": 0}
    call(5 * HOUR, scan_id=scan, **failed, output_tokens=0, cost_micros=0)
    assert store.considered_until(brand.id) is None  # what that call held must be sent again
