"""Enrichment rules enforced by Postgres itself (data-model.md §6)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, delete, text
from sqlalchemy.exc import IntegrityError

from serpsense.domain.enums import Topic
from tests.integration.db_helpers import (
    NOW,
    add,
    add_app,
    add_brand,
    add_mention,
    add_scan,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

ENRICHMENTS = table("enrichments")
PROMPT = "label_mentions/v1"


def call(conn: Connection, user_id: uuid.UUID, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "task": "label_mentions",
        "requested_model": "claude-opus-5-5",
        "served_model": "claude-opus-5-5",
        "prompt_version": PROMPT,
        "request_settings": {"effort": "low"},
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_micros": 900,
        "currency": "USD",
        "stop_reason": "end_turn",
        "outcome": "succeeded",
        "latency_ms": 800,
        "created_at": NOW,
    }
    return add(conn, table("llm_calls"), user_id=user_id, **{**values, **overrides})


def review(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """An owner and a Play review with one edit (revision 2)."""
    owner = add_user(conn)
    brand_id = add_brand(conn, owner)
    mention_id = add_mention(
        conn,
        brand_id,
        source="play_review",
        identity_key="gp:AOqpTO1",
        text="Driver was late",
        url=None,
        outlet=None,
        brand_app_id=add_app(conn, brand_id),
    )
    revision = {"mention_id": mention_id, "revision": 2, "text": "Driver cancelled twice"}
    conn.execute(
        table("mention_revisions")
        .insert()
        .values(scan_id=add_scan(conn, brand_id), created_at=NOW, **revision)
    )
    return owner, mention_id


def label(conn: Connection, mention_id: uuid.UUID, call_id: uuid.UUID, **kw: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "revision": 1,
        "prompt_version": PROMPT,
        "sentiment": -1,
        "severity": 70,
        "topic": "reliability",
        "is_complaint": True,
        "is_about_brand": True,
        "reason": "Complains about a late driver",
        "created_at": NOW,
    }
    return add(conn, ENRICHMENTS, mention_id=mention_id, llm_call_id=call_id, **{**values, **kw})


def test_each_text_of_a_mention_gets_its_own_labels(conn: Connection) -> None:
    owner, mention_id = review(conn)
    call_id = call(conn, owner)
    label(conn, mention_id, call_id)
    label(conn, mention_id, call_id, revision=2, topic="reliability", severity=85)
    v2 = call(conn, owner, prompt_version="label_mentions/v2")
    label(conn, mention_id, v2, prompt_version="label_mentions/v2")  # a new prompt relabels
    with pytest.raises(IntegrityError) as exc:
        label(conn, mention_id, call_id)
    assert violation(exc).constraint_name == "uq_enrichments_mention_id_revision_prompt_version"


def test_a_revision_the_mention_doesnt_have_is_refused(conn: Connection) -> None:
    owner, mention_id = review(conn)
    with pytest.raises(IntegrityError) as exc:
        label(conn, mention_id, call(conn, owner), revision=3)
    assert violation(exc).constraint_name == "ck_enrichments_revision_exists"


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"revision": 0}, "revision_positive"),
        ({"sentiment": 2}, "sentiment_range"),
        ({"severity": 101}, "severity_range"),
        ({"reason": "  "}, "reason_length"),
        ({"reason": "x" * 501}, "reason_length"),
    ],
)
def test_enrichment_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    owner, mention_id = review(conn)
    with pytest.raises(IntegrityError) as exc:
        label(conn, mention_id, call(conn, owner), **overrides)
    assert violation(exc).constraint_name == f"ck_enrichments_{check}"


def test_a_labelled_mention_cannot_be_deleted(conn: Connection) -> None:
    owner = add_user(conn)
    mention_id = add_mention(conn, add_brand(conn, owner))  # a news article, no revisions
    label(conn, mention_id, call(conn, owner))
    with pytest.raises(IntegrityError) as exc:
        conn.execute(delete(table("mentions")).where(table("mentions").c.id == mention_id))
    assert violation(exc).constraint_name == "fk_enrichments_mention_id_mentions"


def test_topics_match_domain(conn: Connection) -> None:
    labels = conn.execute(text("SELECT unnest(enum_range(NULL::topic))::text")).scalars()
    assert list(labels) == [topic.value for topic in Topic]


def test_labels_come_from_a_labelling_task(conn: Connection) -> None:
    owner, mention_id = review(conn)
    draft = {"task": "draft_response", "prompt_version": "draft_response/v1"}
    with pytest.raises(IntegrityError) as exc:
        label(conn, mention_id, call(conn, owner, **draft), prompt_version="draft_response/v1")
    assert violation(exc).constraint_name == "ck_enrichments_prompt_from_labelling_task"
