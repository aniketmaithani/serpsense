"""Draft replies from where the owner works: a button on the brand page's stories and negative
mentions, and the drafts page listing every draft with a copy button; the owner's only."""

import uuid

import pytest
from sqlalchemy import Engine, insert

from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.domain.enums import CrisisComponent, Surface, Topic
from serpsense.ports.enrichment_store import MentionLabel
from tests.integration.db_helpers import NOW, add_brand, add_llm_call, add_scan, table
from tests.integration.test_draft_pages import signed_in
from tests.integration.test_login_pages import browser, token
from tests.integration.test_scan_scores_view import scored
from tests.integration.test_story_pages import assign, mention, story

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

CALM = {c: 0 for c in CrisisComponent}


def complained(engine: Engine, owner: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A brand whose latest scan saw one labelled complaint, placed in a story."""
    with engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
        scan = add_scan(conn, ola, status="succeeded")
        scored(conn, scan, {Surface.NEWS: 40}, CALM)
        said = mention(conn, ola, "Driver asked ₹200 extra at the airport")
        conn.execute(insert(table("mention_observations")).values(mention_id=said, scan_id=scan))
        label = MentionLabel(said, 1, -1, 60, Topic.PRICING, True, True, "Asked for extra cash.")
        call = add_llm_call(conn, owner)
        SqlEnrichmentStore(conn).record([label], prompt_version="label_mentions/v1",
                                        llm_call_id=call, at=NOW)  # fmt: skip
        fares = story(conn, owner, ola, "Extra cash at airports")
        assign(conn, owner, fares, said)
    return ola, fares


def test_a_reply_drafted_from_the_brand_page_lands_on_the_drafts_page(
    committing_engine: Engine,
) -> None:
    client, owner = signed_in(committing_engine, live=True)
    assert "No drafts yet" in client.get("/drafts").text
    ola, fares = complained(committing_engine, owner)
    page = client.get(f"/brands/{ola}").text
    action = f'action="/brands/{ola}/narratives/{fares}/drafts"'
    assert page.count(action) == 2 and page.count('class="inline reply"') == 2  # story, mention
    ask = {"csrf_token": token(page), "kind": "review_reply", "preset": "standard"}
    asked = client.post(f"/brands/{ola}/narratives/{fares}/drafts", data=ask)
    assert asked.headers["location"].endswith("?draft=drafted")

    drafts = client.get("/drafts").text
    assert "We&#39;re sorry about the airport pickup." in drafts and "Copy" in drafts
    assert "Extra cash at airports" in drafts and "Review reply · Standard" in drafts
    assert "Driver asked ₹200 extra at the airport" in drafts  # what it responds to
    assert "/static/copy.js" in drafts and 'aria-current="page">Drafts' in drafts

    other, _ = signed_in(committing_engine, live=True)
    assert "airport pickup" not in other.get("/drafts").text
    assert browser(committing_engine).get("/drafts").headers["location"] == "/login"


def test_no_reply_buttons_without_a_live_model(committing_engine: Engine) -> None:
    client, owner = signed_in(committing_engine, live=False)
    ola, _ = complained(committing_engine, owner)
    page = client.get(f"/brands/{ola}").text
    assert "Extra cash at airports" in page and 'class="inline reply"' not in page
