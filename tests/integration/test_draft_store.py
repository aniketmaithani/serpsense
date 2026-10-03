"""What a draft is written from, and the drafts written, on Postgres: the owner's only, a story's
current mentions, and citations kept with each draft (data-model §6)."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection

from serpsense.adapters.db.draft_store import SqlDraftStore
from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.domain.enums import DraftKind, DraftPreset, MentionSource, Topic
from serpsense.ports.drafts import NewDraft
from serpsense.ports.enrichment_store import MentionLabel
from tests.integration.db_helpers import (
    NOW,
    add,
    add_brand,
    add_llm_call,
    add_mention,
    add_user,
    table,
)

pytestmark = pytest.mark.integration

GROUP = {"task": "group_narratives", "prompt_version": "group_narratives/v1"}
DRAFT = {"task": "draft_response", "prompt_version": "draft_response/v1"}


class Story:
    def __init__(self, conn: Connection) -> None:
        self.conn, self.owner = conn, add_user(conn)
        self.brand = add_brand(conn, self.owner, name="Ola", slug="ola")
        self.story = self.narrative("Extra cash at airports")

    def narrative(self, label: str) -> uuid.UUID:
        call = add_llm_call(self.conn, self.owner, **GROUP)
        values = {"label": label, "summary": f"{label}.", "created_at": NOW}
        return add(self.conn, table("narratives"), brand_id=self.brand, llm_call_id=call,
                   prompt_version="group_narratives/v1", **values)  # fmt: skip

    def mention(self, text: str, sentiment: int | None) -> uuid.UUID:
        key = hashlib.sha256(text.encode()).hexdigest()
        mention = add_mention(self.conn, self.brand, identity_key=key, text=text,
                              url=f"https://n.in/{key[:8]}")  # fmt: skip
        if sentiment is not None:
            label = MentionLabel(mention, 1, sentiment, 50, Topic.PRICING, True, True, "Why.")
            call = add_llm_call(self.conn, self.owner)
            SqlEnrichmentStore(self.conn).record(
                [label], prompt_version="label_mentions/v1", llm_call_id=call, at=NOW
            )
        return mention

    def assign(self, story: uuid.UUID, mention: uuid.UUID, minutes: int = 0) -> None:
        call = add_llm_call(self.conn, self.owner, **GROUP)
        add(self.conn, table("narrative_assignments"), narrative_id=story, mention_id=mention,
            llm_call_id=call, prompt_version="group_narratives/v1",
            created_at=NOW + timedelta(minutes=minutes))  # fmt: skip


@pytest.fixture
def ola(conn: Connection) -> Story:
    return Story(conn)


def test_a_draft_is_written_from_the_storys_current_mentions_worst_first(ola: Story) -> None:
    kind = ola.mention("Fare was fine", 1)
    cash = ola.mention("Driver asked ₹200 extra", -1)
    moved = ola.mention("Driver late", -1)
    for mention in (kind, cash, moved):
        ola.assign(ola.story, mention)
    ola.assign(ola.narrative("Late drivers"), moved, minutes=5)  # moved to another story since
    store = SqlDraftStore(ola.conn)
    material = store.material(ola.owner, ola.brand, ola.story, limit=25)
    assert material is not None and (material.owner_id, material.brand_name) == (ola.owner, "Ola")
    assert (material.story_label, material.story_summary) == (
        "Extra cash at airports", "Extra cash at airports.",
    )  # fmt: skip
    assert [(m.mention_id, m.sentiment) for m in material.mentions] == [(cash, -1), (kind, 1)]
    assert material.mentions[0].source is MentionSource.NEWS


def test_material_and_drafts_are_the_owners_only(ola: Story) -> None:
    store, stranger = SqlDraftStore(ola.conn), add_user(ola.conn, "else@example.com")
    other_brand = add_brand(ola.conn, ola.owner, name="Uber", slug="uber")
    for user, brand, story in (
        (stranger, ola.brand, ola.story),  # someone else
        (ola.owner, other_brand, ola.story),  # my story under my other brand
        (ola.owner, ola.brand, uuid.uuid4()),
    ):
        assert store.material(user, brand, story, limit=25) is None
        assert store.drafts(user, brand, story, limit=5) == []


def test_drafts_are_kept_with_their_citations_newest_first(ola: Story) -> None:
    cash = ola.mention("Driver asked ₹200 extra", -1)
    ola.assign(ola.story, cash)
    store = SqlDraftStore(ola.conn)
    written = []
    for minutes, preset in enumerate((DraftPreset.STANDARD, DraftPreset.HIGH_THINKING)):
        call = add_llm_call(ola.conn, ola.owner, **DRAFT)
        summary = "Checked airports first." if preset is DraftPreset.HIGH_THINKING else None
        draft = NewDraft(
            narrative_id=ola.story, kind=DraftKind.HOLDING_STATEMENT, preset=preset,
            text=f"We're looking into it ({minutes}).", reasoning_summary=summary, cited=[cash],
            prompt_version="draft_response/v1", llm_call_id=call,
            created_at=NOW + timedelta(minutes=minutes),
        )  # fmt: skip
        written.append(store.record(draft))
    rows = store.drafts(ola.owner, ola.brand, ola.story, limit=5)
    assert [r.draft_id for r in rows] == written[::-1]
    newest = rows[0]
    assert (newest.preset, newest.reasoning_summary) == (
        DraftPreset.HIGH_THINKING, "Checked airports first.",
    )  # fmt: skip
    assert [(c.mention_id, c.text) for c in newest.cited] == [(cash, "Driver asked ₹200 extra")]
