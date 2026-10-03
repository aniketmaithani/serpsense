"""Enrichment rules enforced by Postgres itself (data-model.md §6)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, text, update
from sqlalchemy.exc import IntegrityError

from serpsense.domain.enums import Topic
from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add,
    add_app,
    add_brand,
    add_llm_call,
    add_mention,
    add_scan,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

ENRICHMENTS = table("enrichments")
PROMPT = "label_mentions/v1"


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
    call_id = add_llm_call(conn, owner)
    label(conn, mention_id, call_id)
    label(conn, mention_id, call_id, revision=2, topic="reliability", severity=85)
    v2 = add_llm_call(conn, owner, prompt_version="label_mentions/v2")
    label(conn, mention_id, v2, prompt_version="label_mentions/v2")  # a new prompt relabels
    with pytest.raises(IntegrityError) as exc:
        label(conn, mention_id, call_id)
    assert violation(exc).constraint_name == "uq_enrichments_mention_id_revision_prompt_version"


def test_a_revision_the_mention_doesnt_have_is_refused(conn: Connection) -> None:
    owner, mention_id = review(conn)
    with pytest.raises(IntegrityError) as exc:
        label(conn, mention_id, add_llm_call(conn, owner), revision=3)
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
        label(conn, mention_id, add_llm_call(conn, owner), **overrides)
    assert violation(exc).constraint_name == f"ck_enrichments_{check}"


def test_a_labelled_mention_cannot_be_deleted(conn: Connection) -> None:
    owner = add_user(conn)
    mention_id = add_mention(conn, add_brand(conn, owner))  # a news article, no revisions
    label(conn, mention_id, add_llm_call(conn, owner))
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
        label(
            conn, mention_id, add_llm_call(conn, owner, **draft), prompt_version="draft_response/v1"
        )
    assert violation(exc).constraint_name == "ck_enrichments_prompt_from_labelling_task"


@pytest.mark.parametrize(
    "call_overrides",
    [
        {"outcome": "invalid_output"},
        {"outcome": "failed", "served_model": None, "stop_reason": None},
        {"prompt_version": "label_mentions/v2"},  # labels must say which prompt made them
    ],
)
def test_labels_come_from_a_successful_call_with_their_prompt(
    conn: Connection, call_overrides: dict[str, Any]
) -> None:
    owner, mention_id = review(conn)
    with pytest.raises(IntegrityError) as exc:
        label(conn, mention_id, add_llm_call(conn, owner, **call_overrides))
    assert violation(exc).constraint_name == "ck_enrichments_from_call"


def test_labels_come_from_a_call_for_the_brands_owner(conn: Connection) -> None:
    _, mention_id = review(conn)
    stranger = add_user(conn, "stranger@example.com")
    with pytest.raises(IntegrityError) as exc:
        label(conn, mention_id, add_llm_call(conn, stranger))
    assert violation(exc).constraint_name == "ck_enrichments_from_call"


def test_only_the_reason_can_be_rewritten_and_labels_are_never_deleted(conn: Connection) -> None:
    owner, mention_id = review(conn)
    enrichment_id = label(conn, mention_id, add_llm_call(conn, owner))
    row = ENRICHMENTS.c.id == enrichment_id
    conn.execute(update(ENRICHMENTS).where(row).values(reason="[removed]"))  # a scrub
    for change in ({"sentiment": 1}, {"topic": "pricing"}, {"mention_id": uuid.uuid4()}):
        with pytest.raises(IntegrityError) as exc, conn.begin_nested():
            conn.execute(update(ENRICHMENTS).where(row).values(**change))
        assert violation(exc).constraint_name == "ck_enrichments_identity_immutable"
    statements: list[Executable] = [delete(ENRICHMENTS), text("TRUNCATE enrichments CASCADE")]
    for statement in statements:
        with pytest.raises(IntegrityError) as exc, conn.begin_nested():
            conn.execute(statement)
        assert violation(exc).sqlstate == RESTRICT_VIOLATION


def test_every_column_but_the_reason_is_frozen(conn: Connection) -> None:
    """A column added later must be added to the freeze too."""
    args = conn.execute(
        text("SELECT tgargs FROM pg_trigger WHERE tgname = 'trg_enrichments_identity_immutable'")
    ).scalar_one()
    frozen = set(bytes(args).decode().split("\x00")[1:-1])  # after the constraint name
    assert frozen == {column.name for column in ENRICHMENTS.columns} - {"reason"}
