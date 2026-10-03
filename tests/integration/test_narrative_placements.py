"""The narrative store on real Postgres: open narratives, and a grouping call's placements."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, select

from serpsense.adapters.db.narrative_store import SqlNarrativeStore
from serpsense.ports.narrative_store import Assignment, NarrativeStore, NewNarrative
from tests.integration.db_helpers import NOW, add_llm_call, table
from tests.integration.test_narrative_store import WEEK, Brand
from tests.integration.test_narratives_schema import GROUPING, assign

pytestmark = pytest.mark.integration


@pytest.fixture
def store(conn: Connection) -> NarrativeStore:
    return SqlNarrativeStore(conn)


def story(brand: Brand, label: str = "Fares above the quote") -> NewNarrative:
    return NewNarrative(uuid.uuid4(), brand.id, label, f"Riders report: {label.lower()}.")


def record(
    store: NarrativeStore, call: uuid.UUID, *placed: tuple[uuid.UUID, uuid.UUID], **kw: Any
) -> int:
    new, at = kw.pop("new", ()), kw.pop("at", NOW)
    made = {"prompt_version": "group_narratives/v1", "llm_call_id": call, "at": at}
    return store.record(new, [Assignment(*pair) for pair in placed], **made)


def test_open_narratives_count_their_current_mentions_latest_joined_first(
    conn: Connection, store: NarrativeStore
) -> None:
    brand = Brand(conn)
    call = add_llm_call(conn, brand.owner, **GROUPING)
    late, fares, stale = story(brand, "Late drivers"), story(brand), story(brand, "Old outage")
    one, two, three, four = (brand.mention() for _ in range(4))
    record(store, call, (three, stale.narrative_id), new=[stale], at=NOW - 3 * WEEK)
    record(store, call, (one, late.narrative_id), (two, late.narrative_id), new=[late])
    record(store, call, (four, fares.narrative_id), new=[fares])
    assign(conn, fares.narrative_id, two, call, created_at=NOW + timedelta(minutes=1))  # moved
    rival = Brand(conn, "rival")
    theirs = story(rival)
    their_call = add_llm_call(conn, rival.owner, **GROUPING)
    record(store, their_call, (rival.mention(), theirs.narrative_id), new=[theirs])

    found = store.open(brand.id, active_since=NOW - 2 * WEEK, limit=10)
    got = [(n.narrative_id, n.label, n.mentions) for n in found]
    assert got == [(fares.narrative_id, fares.label, 2), (late.narrative_id, "Late drivers", 1)]
    assert found[0].summary == "Riders report: fares above the quote."
    assert [n.narrative_id for n in store.open(rival.id, active_since=NOW, limit=10)] == [
        theirs.narrative_id
    ]
    assert len(store.open(brand.id, active_since=NOW - 2 * WEEK, limit=1)) == 1


def test_a_calls_placements_are_stored_once_with_provenance(
    conn: Connection, store: NarrativeStore
) -> None:
    brand = Brand(conn)
    call = add_llm_call(conn, brand.owner, **GROUPING)
    fares, nobody, mention = story(brand), story(brand, "Nobody joins"), brand.mention()
    assert record(store, call, (mention, fares.narrative_id), new=[fares, nobody]) == 1
    later, again = NOW + timedelta(hours=12), story(brand, "A retry's new story")
    retried = record(store, call, (mention, again.narrative_id), new=[again], at=later)
    assert retried == 0 and record(store, call) == 0  # a retry keeps the mention's story
    narratives, assignments = table("narratives"), table("narrative_assignments")
    rows = conn.execute(select(narratives).where(narratives.c.brand_id == brand.id)).all()
    assert [(r.id, r.prompt_version, r.llm_call_id) for r in rows] == [
        (fares.narrative_id, "group_narratives/v1", call)  # nobody's story isn't stored
    ]
    placed = conn.execute(select(assignments).where(assignments.c.mention_id == mention)).one()
    assert (placed.narrative_id, placed.llm_call_id) == (fares.narrative_id, call)


@pytest.mark.parametrize(
    ("label", "summary"), [(" ", "A summary."), ("x" * 121, "A summary."), ("Fares", "")]
)
def test_a_new_narrative_needs_a_label_and_summary_the_table_holds(
    label: str, summary: str
) -> None:
    with pytest.raises(ValueError, match="narrative's"):
        NewNarrative(uuid.uuid4(), uuid.uuid4(), label, summary)
