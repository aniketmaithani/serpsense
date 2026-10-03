"""Draft and citation rules enforced by Postgres itself (data-model §6)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, func, insert, text, update
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

DRAFT = {"task": "draft_response", "prompt_version": "draft_response/v1"}
GROUP = {"task": "group_narratives", "prompt_version": "group_narratives/v1"}


class World:
    """An owner's brand with a narrative and a mention, and a stranger."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.owner, self.stranger = add_user(conn), add_user(conn, "else@example.com")
        self.brand = add_brand(conn, self.owner)
        self.mention = add_mention(conn, self.brand)
        story = {"label": "Airport surge", "summary": "Fares triple.", "created_at": NOW}
        call = add_llm_call(conn, self.owner, **GROUP)
        self.narrative = add(
            conn, table("narratives"), brand_id=self.brand, llm_call_id=call,
            prompt_version="group_narratives/v1", **story,
        )  # fmt: skip

    def draft(self, call: uuid.UUID | None = None, **kw: Any) -> uuid.UUID:
        values = {"kind": "holding_statement", "preset": "standard", "text": "We are aware…"}
        call = call or add_llm_call(self.conn, self.owner, **DRAFT)
        values |= {"prompt_version": "draft_response/v1", "created_at": NOW, **kw}
        return add(self.conn, table("drafts"), narrative_id=self.narrative, llm_call_id=call,
                   **values)  # fmt: skip


def cite(conn: Connection, draft: uuid.UUID, mention: uuid.UUID) -> None:
    conn.execute(insert(table("draft_citations")).values(draft_id=draft, mention_id=mention))


@pytest.fixture
def world(conn: Connection) -> World:
    return World(conn)


@pytest.mark.parametrize(
    "call_overrides",
    [
        {"outcome": "truncated", "stop_reason": "max_tokens"},
        {"prompt_version": "draft_response/v2"},
        {"for_stranger": True},
    ],
)
def test_a_draft_comes_from_a_successful_call_for_the_brands_owner(
    world: World, call_overrides: dict[str, Any]
) -> None:
    user = world.stranger if call_overrides.pop("for_stranger", False) else world.owner
    call = add_llm_call(world.conn, user, **{**DRAFT, **call_overrides})
    with pytest.raises(IntegrityError) as exc:
        world.draft(call)
    assert violation(exc).constraint_name == "ck_drafts_from_call"


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"text": ""}, "ck_drafts_text_length"),
        ({"text": "x" * 4001}, "ck_drafts_text_length"),
        ({"reasoning_summary": "x" * 8001}, "ck_drafts_reasoning_summary_length"),
    ],
)
def test_draft_lengths(world: World, overrides: dict[str, Any], check: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        world.draft(**overrides)
    assert violation(exc).constraint_name == check


def test_a_call_writes_one_draft(world: World) -> None:
    call = add_llm_call(world.conn, world.owner, **DRAFT)
    world.draft(call)
    with pytest.raises(IntegrityError) as exc:
        world.draft(call)
    assert violation(exc).constraint_name == "uq_drafts_llm_call_id"


def test_a_draft_cites_only_its_own_brands_mentions(world: World) -> None:
    draft = world.draft(reasoning_summary="Checked the fares first.", preset="high_thinking")
    cite(world.conn, draft, world.mention)
    other = add_mention(world.conn, add_brand(world.conn, world.owner, slug="rival"))
    with pytest.raises(IntegrityError) as exc:
        cite(world.conn, draft, other)
    assert violation(exc).constraint_name == "ck_draft_citations_same_brand"


@pytest.mark.parametrize("name", ["drafts", "draft_citations"])
@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_drafts_and_citations_are_append_only(world: World, name: str, mutation: str) -> None:
    cite(world.conn, world.draft(), world.mention)
    target = table(name)
    column = "mention_id" if name == "draft_citations" else "created_at"
    value = world.mention if name == "draft_citations" else func.now()
    statements: dict[str, Executable] = {
        "update": update(target).values({column: value}),
        "delete": delete(target),
        "truncate": text(f"TRUNCATE {name} CASCADE"),
    }
    with pytest.raises(IntegrityError) as exc:
        world.conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION
