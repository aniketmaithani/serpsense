"""Alert explanation rules enforced by Postgres itself (data-model §6)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, Executable, delete, func, text, update
from sqlalchemy.exc import IntegrityError

from tests.integration.db_helpers import (
    NOW,
    RESTRICT_VIOLATION,
    add,
    add_brand,
    add_llm_call,
    add_scan,
    add_user,
    table,
    violation,
)

pytestmark = pytest.mark.integration

EXPLAIN = {"task": "explain_crisis", "prompt_version": "explain_crisis/v1"}
GROUP = {"task": "group_narratives", "prompt_version": "group_narratives/v1"}


class World:
    """An owner's brand with a scored scan's alert, and a stranger."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.owner, self.stranger = add_user(conn), add_user(conn, "else@example.com")
        self.brand = add_brand(conn, self.owner)
        scan = add_scan(conn, self.brand, status="succeeded")
        self.alert = add(conn, table("alerts"), scan_id=scan, rule="level_increase", created_at=NOW)

    def explanation(self, call: uuid.UUID | None = None, **kw: Any) -> uuid.UUID:
        values = {"text": "The crisis level rose because…", "prompt_version": "explain_crisis/v1"}
        call = call or add_llm_call(self.conn, self.owner, **EXPLAIN)
        values |= {"created_at": NOW, **kw}
        return add(self.conn, table("alert_explanations"), alert_id=self.alert, llm_call_id=call,
                   **values)  # fmt: skip


@pytest.fixture
def world(conn: Connection) -> World:
    return World(conn)


def test_an_alert_has_one_explanation(world: World) -> None:
    world.explanation()
    with pytest.raises(IntegrityError) as exc:
        world.explanation()
    assert violation(exc).constraint_name == "uq_alert_explanations_alert_id"


def test_a_call_explains_one_alert(world: World) -> None:
    call = add_llm_call(world.conn, world.owner, **EXPLAIN)
    world.explanation(call)
    scan = add_scan(world.conn, world.brand, status="succeeded", scheduled_for=None,
                    trigger="manual", requested_by=world.owner)  # fmt: skip
    world.alert = add(world.conn, table("alerts"), scan_id=scan, rule="level_increase",
                      created_at=NOW)  # fmt: skip
    with pytest.raises(IntegrityError) as exc:
        world.explanation(call)
    assert violation(exc).constraint_name == "uq_alert_explanations_llm_call_id"


@pytest.mark.parametrize(
    "call_overrides",
    [
        {"outcome": "refused", "stop_reason": "refusal"},
        {"prompt_version": "explain_crisis/v2"},
        {"for_stranger": True},
    ],
)
def test_an_explanation_comes_from_a_successful_call_for_the_brands_owner(
    world: World, call_overrides: dict[str, Any]
) -> None:
    user = world.stranger if call_overrides.pop("for_stranger", False) else world.owner
    call = add_llm_call(world.conn, user, **{**EXPLAIN, **call_overrides})
    with pytest.raises(IntegrityError) as exc:
        world.explanation(call)
    assert violation(exc).constraint_name == "ck_alert_explanations_from_call"


@pytest.mark.parametrize("words", [" ", "x" * 2001])
def test_an_explanation_has_some_words_and_not_too_many(world: World, words: str) -> None:
    with pytest.raises(IntegrityError) as exc:
        world.explanation(text=words)
    assert violation(exc).constraint_name == "ck_alert_explanations_text_length"


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
def test_explanations_are_append_only(world: World, mutation: str) -> None:
    world.explanation()
    target = table("alert_explanations")
    statements: dict[str, Executable] = {
        "update": update(target).values(created_at=func.now()),
        "delete": delete(target),
        "truncate": text("TRUNCATE alert_explanations"),
    }
    with pytest.raises(IntegrityError) as exc:
        world.conn.execute(statements[mutation])
    assert violation(exc).sqlstate == RESTRICT_VIOLATION


def test_narratives_still_check_their_calls(world: World) -> None:
    """The extended provenance function keeps its older ways to the brand."""
    call = add_llm_call(world.conn, world.stranger, **GROUP)
    story = {"label": "x", "summary": "y", "prompt_version": "group_narratives/v1"}
    with pytest.raises(IntegrityError) as exc:
        add(world.conn, table("narratives"), brand_id=world.brand, llm_call_id=call,
            created_at=NOW, **story)  # fmt: skip
    assert violation(exc).constraint_name == "ck_narratives_from_call"
