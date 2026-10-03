"""What keeps drafting from running away, on real Postgres: one lease holder at a time, and a
count of the day's high-thinking and max draft calls (failed ones too)."""

from datetime import timedelta

import pytest
from sqlalchemy import Connection, Engine

from serpsense.adapters.db.draft_store import SqlDraftStore
from serpsense.adapters.db.leases import SqlLeases
from tests.integration.db_helpers import NOW, add_llm_call, add_user

pytestmark = pytest.mark.integration

DRAFT = {"task": "draft_response", "prompt_version": "draft_response/v1"}


def test_a_lease_has_one_holder_until_its_block_ends(committing_engine: Engine) -> None:
    leases = SqlLeases(committing_engine)
    with (
        leases.hold("draft:a") as first,
        leases.hold("draft:a") as second,  # a second real session
        leases.hold("draft:b") as other,
    ):
        assert (first, second, other) == (True, False, True)
    with leases.hold("draft:a") as again:
        assert again  # released with the block
    with pytest.raises(LookupError), leases.hold("draft:a"):
        raise LookupError
    with leases.hold("draft:a") as after_an_error:
        assert after_an_error


def test_the_days_thinking_drafts_count_failed_calls_and_only_this_users(
    conn: Connection,
) -> None:
    owner, other = add_user(conn), add_user(conn, "else@example.com")
    thinking = {**DRAFT, "request_settings": {"effort": "xhigh"}}
    add_llm_call(conn, owner, **thinking)
    add_llm_call(conn, owner, **{**DRAFT, "request_settings": {"effort": "max"}},
                 outcome="failed", served_model=None, stop_reason=None)  # fmt: skip
    add_llm_call(conn, owner, **DRAFT, created_at=NOW)  # standard: "high" effort isn't counted
    add_llm_call(conn, owner, **thinking, created_at=NOW - timedelta(days=1))  # yesterday
    add_llm_call(conn, other, **thinking)
    add_llm_call(conn, owner, request_settings={"effort": "max"})  # another task
    store = SqlDraftStore(conn)
    assert store.thinking_drafts_since(owner, NOW - timedelta(hours=1)) == 2
