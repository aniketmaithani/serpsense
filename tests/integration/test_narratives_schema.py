"""Narrative and assignment rules enforced by Postgres itself (data-model.md §6)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add,
    add_brand,
    add_llm_call,
    add_mention,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

NARRATIVES, ASSIGNMENTS = table("narratives"), table("narrative_assignments")
GROUPING = {"task": "group_narratives", "prompt_version": "group_narratives/v1"}


def narrative(conn: Connection, brand_id: uuid.UUID, call_id: uuid.UUID, **kw: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "label": "Late drivers in Bengaluru",
        "summary": "Riders report drivers arriving late or cancelling after acceptance.",
        "prompt_version": "group_narratives/v1",
        "created_at": NOW,
    }
    return add(conn, NARRATIVES, brand_id=brand_id, llm_call_id=call_id, **{**values, **kw})


def assign(
    conn: Connection, narrative_id: uuid.UUID, mention_id: uuid.UUID, call_id: uuid.UUID, **kw: Any
) -> uuid.UUID:
    values = {"narrative_id": narrative_id, "mention_id": mention_id, "llm_call_id": call_id}
    defaults = {"prompt_version": "group_narratives/v1", "created_at": NOW}
    return add(conn, ASSIGNMENTS, **{**defaults, **values, **kw})


def setup(conn: Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """An owner's brand, one of its mentions, and a grouping call."""
    owner = add_user(conn)
    brand_id = add_brand(conn, owner)
    return brand_id, add_mention(conn, brand_id), add_llm_call(conn, owner, **GROUPING)


def test_the_latest_assignment_of_a_mention_wins(conn: Connection) -> None:
    brand_id, mention_id, call_id = setup(conn)
    late, refunds = (
        narrative(conn, brand_id, call_id, label=label) for label in ("Late", "Refunds")
    )
    for minutes, story in enumerate((late, refunds, late)):  # A → B → A is allowed
        assign(conn, story, mention_id, call_id, created_at=NOW + timedelta(minutes=minutes))
    latest = (
        select(ASSIGNMENTS.c.narrative_id)
        .where(ASSIGNMENTS.c.mention_id == mention_id)
        .order_by(ASSIGNMENTS.c.created_at.desc())
        .limit(1)
    )
    assert conn.execute(latest).scalar_one() == late
    with pytest.raises(IntegrityError) as exc:
        assign(conn, refunds, mention_id, call_id)  # same instant as the first
    assert violation(exc).constraint_name == "uq_narrative_assignments_mention_id_created_at"


def test_a_narrative_groups_only_its_own_brands_mentions(conn: Connection) -> None:
    brand_id, _, call_id = setup(conn)
    story = narrative(conn, brand_id, call_id)
    owner = conn.execute(select(table("brands").c.owner_id)).scalar_one()
    other = add_mention(conn, add_brand(conn, owner, slug="rival"))  # the owner's other brand
    with pytest.raises(IntegrityError) as exc:
        assign(conn, story, other, call_id)
    assert violation(exc).constraint_name == "ck_narrative_assignments_same_brand"


@pytest.mark.parametrize(
    ("table_name", "call_overrides"),
    [
        ("narratives", {"outcome": "truncated", "stop_reason": "max_tokens"}),
        ("narratives", {"prompt_version": "group_narratives/v2"}),
        ("narrative_assignments", {"outcome": "refused", "stop_reason": "refusal"}),
        ("narrative_assignments", {"prompt_version": "group_narratives/v2"}),
    ],
)
def test_model_output_comes_from_a_successful_call(
    conn: Connection, table_name: str, call_overrides: dict[str, Any]
) -> None:
    brand_id, mention_id, good_call = setup(conn)
    owner = conn.execute(select(table("brands").c.owner_id)).scalar_one()
    bad_call = add_llm_call(conn, owner, **{**GROUPING, **call_overrides})
    with pytest.raises(IntegrityError) as exc:
        if table_name == "narratives":
            narrative(conn, brand_id, bad_call)
        else:
            assign(conn, narrative(conn, brand_id, good_call), mention_id, bad_call)
    assert violation(exc).constraint_name == f"ck_{table_name}_from_call"


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"label": " "}, "label_length"),
        ({"label": "x" * 121}, "label_length"),
        ({"summary": ""}, "summary_length"),
        ({"summary": "x" * 2001}, "summary_length"),
    ],
)
def test_narrative_checks(conn: Connection, overrides: dict[str, Any], check: str) -> None:
    brand_id, _, call_id = setup(conn)
    with pytest.raises(IntegrityError) as exc:
        narrative(conn, brand_id, call_id, **overrides)
    assert violation(exc).constraint_name == f"ck_narratives_{check}"


@pytest.mark.parametrize("table_name", ["narratives", "narrative_assignments"])
@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_narratives_and_assignments_are_append_only(
    conn: Connection, table_name: str, mutation: str
) -> None:
    brand_id, mention_id, call_id = setup(conn)
    assign(conn, narrative(conn, brand_id, call_id), mention_id, call_id)
    target = table(table_name)
    statements: dict[str, Executable] = {
        "update": update(target).values(created_at=func.now()),
        "delete": delete(target),
        "truncate": text(f"TRUNCATE {table_name} CASCADE"),
    }
    with pytest.raises(IntegrityError) as exc:
        conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


@pytest.mark.parametrize("table_name", ["narratives", "narrative_assignments"])
def test_output_comes_from_a_call_for_the_brands_owner(conn: Connection, table_name: str) -> None:
    brand_id, mention_id, call_id = setup(conn)
    stranger = add_llm_call(conn, add_user(conn, "stranger@example.com"), **GROUPING)
    with pytest.raises(IntegrityError) as exc:
        if table_name == "narratives":
            narrative(conn, brand_id, stranger)
        else:
            assign(conn, narrative(conn, brand_id, call_id), mention_id, stranger)
    assert violation(exc).constraint_name == f"ck_{table_name}_from_call"


@pytest.mark.parametrize("table_name", ["narratives", "narrative_assignments"])
def test_output_comes_from_the_grouping_task(conn: Connection, table_name: str) -> None:
    brand_id, mention_id, call_id = setup(conn)
    owner = conn.execute(select(table("brands").c.owner_id)).scalar_one()
    explain = {"task": "explain_crisis", "prompt_version": "explain_crisis/v1"}
    other = add_llm_call(conn, owner, **explain)
    with pytest.raises(IntegrityError) as exc:
        if table_name == "narratives":
            narrative(conn, brand_id, other, prompt_version="explain_crisis/v1")
        else:
            story = narrative(conn, brand_id, call_id)
            assign(conn, story, mention_id, other, prompt_version="explain_crisis/v1")
    assert violation(exc).constraint_name == f"ck_{table_name}_prompt_from_grouping_task"
