"""Drafting on a story's page over HTTP (BUILD_PLAN §13, ADR-0008): CSRF-checked buttons, drafts
shown as AI-generated text to copy with what they cite, and nothing for anyone else's story."""

import json
import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from serpsense.adapters.db.llm_profiles import SqlLlmProfiles
from serpsense.config import Settings
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import HAIKU, Effort, LlmPreset
from serpsense.domain.settings.llm import LlmProfile, TaskChoice
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from tests.factories import make_settings
from tests.fakes import ScriptedLlm
from tests.integration.db_helpers import NOW, add_brand, add_user, table
from tests.integration.test_login_pages import browser, code_for, token
from tests.integration.test_overview_pages import HOSTILE
from tests.integration.test_story_pages import assign, mention, story

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

LIVE = make_settings(anthropic_api_key="sk-ant-test-only")
Answer = Callable[[LlmRequest], str | LlmCallFailed]


def reply(request: LlmRequest) -> str:
    text = f"We're sorry about the airport pickup. {HOSTILE}"
    return json.dumps({"text": text, "cited": ["m1"]})


def unrecorded(request: LlmRequest) -> LlmCallFailed:
    return LlmCallFailed("llm.replay_unrecorded", retryable=False, latency_ms=0)


def signed_in(
    engine: Engine, live: bool, settings: Settings | None = None, answer: Answer = reply
) -> tuple[TestClient, uuid.UUID]:
    chosen = settings or (LIVE if live else None)
    client = browser(engine, settings=chosen, model=ScriptedLlm(answer))
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    form = token(client.get("/login").text)
    client.post("/login", data={"form_token": form, "email": email})
    client.post(
        "/verify", data={"form_token": form, "email": email, "code": code_for(engine, email)}
    )
    with engine.connect() as conn:
        users = table("users")
        return client, conn.execute(select(users.c.id).where(users.c.email == email)).scalar_one()


def ola_story(engine: Engine, owner: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    with engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
        fares = story(conn, owner, ola, "Extra cash at airports")
        assign(conn, owner, fares, mention(conn, ola, "Driver asked ₹200 extra at the airport"))
    return ola, fares


def test_the_owner_drafts_and_copies_a_reply_citing_what_it_answers(
    committing_engine: Engine,
) -> None:
    client, owner = signed_in(committing_engine, live=True)
    ola, fares = ola_story(committing_engine, owner)
    page_url = f"/brands/{ola}/narratives/{fares}"
    page = client.get(page_url).text
    assert "High thinking" in page and "FAQ entry" in page
    ask = {"csrf_token": token(page), "kind": "review_reply", "preset": "high_thinking"}
    url = f"{page_url}/drafts"
    assert client.post(url, data={**ask, "csrf_token": "forged"}).status_code == 403
    asked = client.post(url, data=ask)
    assert asked.status_code == 303 and asked.headers["location"] == f"{page_url}?draft=drafted"
    shown = client.get(asked.headers["location"]).text
    assert "Drafted: read it" in shown and "AI-generated draft" in shown
    assert "We&#39;re sorry about the airport pickup." in shown and HOSTILE not in shown
    assert "Review reply · High thinking" in shown and "draft_response/v1" in shown
    assert shown.count("Driver asked ₹200 extra at the airport") == 2  # the mention, and cited

    with committing_engine.begin() as conn:
        stranger = add_user(conn, f"{uuid.uuid4().hex[:8]}@example.com")
        theirs = add_brand(conn, stranger, name="Theirs", slug=f"t-{uuid.uuid4().hex[:8]}")
        their_story = story(conn, stranger, theirs, "Not yours")
    for brand, narrative in ((theirs, their_story), (ola, their_story), (ola, "x")):
        target = f"/brands/{brand}/narratives/{narrative}/drafts"
        assert client.post(target, data=ask).status_code == 404


def test_replay_mode_drafts_with_no_key_from_what_it_recorded(committing_engine: Engine) -> None:
    replay = make_settings(serpsense_mode="replay")  # no Anthropic key: the recordings answer
    client, owner = signed_in(committing_engine, live=False, settings=replay)
    ola, fares = ola_story(committing_engine, owner)
    page_url = f"/brands/{ola}/narratives/{fares}"
    page = client.get(page_url).text
    assert "Drafting needs an Anthropic key" not in page and "Draft</button>" in page
    ask = {"csrf_token": token(page), "kind": "faq_entry", "preset": "standard"}
    asked = client.post(f"{page_url}/drafts", data=ask)
    assert asked.headers["location"] == f"{page_url}?draft=drafted"
    unrecorded_client, other = signed_in(committing_engine, False, replay, answer=unrecorded)
    brand, story_id = ola_story(committing_engine, other)
    other_url = f"/brands/{brand}/narratives/{story_id}"
    form = {**ask, "csrf_token": token(unrecorded_client.get(other_url).text)}
    nothing = unrecorded_client.post(f"{other_url}/drafts", data=form)
    assert nothing.headers["location"] == f"{other_url}?draft=unrecorded"
    signed_out = browser(committing_engine).post(f"{page_url}/drafts", data=ask)
    assert signed_out.status_code == 303 and signed_out.headers["location"] == "/login"


def test_a_draft_with_contact_details_is_flagged_and_haiku_isnt_offered_thinking(
    committing_engine: Engine,
) -> None:
    def linked(request: LlmRequest) -> str:
        text = "We're sorry. Write to us at https://ola-help.example about your ride, please."
        return json.dumps({"text": text, "cited": ["m1"]})

    client, owner = signed_in(committing_engine, live=True, answer=linked)
    haiku = LlmProfile(tasks={LlmTask.DRAFT_RESPONSE: TaskChoice(model=HAIKU, effort=Effort.LOW)})
    SqlLlmProfiles(committing_engine, LlmPreset.BALANCED).save(owner, haiku, at=NOW)
    ola, fares = ola_story(committing_engine, owner)
    page_url = f"/brands/{ola}/narratives/{fares}"
    page = client.get(page_url).text
    assert "High thinking</button>" not in page and "need Opus 5.5 or Sonnet 5.5" in page
    ask = {"csrf_token": token(page), "kind": "review_reply", "preset": "max"}
    refused = client.post(f"{page_url}/drafts", data=ask)
    assert refused.headers["location"] == f"{page_url}?draft=unsupported"
    drafted = client.post(f"{page_url}/drafts", data={**ask, "preset": "standard"})
    shown = client.get(drafted.headers["location"]).text
    assert "contains a link or contact details" in shown


def test_without_a_live_model_drafting_says_so_instead_of_failing(
    committing_engine: Engine,
) -> None:
    client, owner = signed_in(committing_engine, live=False)
    ola, fares = ola_story(committing_engine, owner)
    page_url = f"/brands/{ola}/narratives/{fares}"
    page = client.get(page_url).text
    assert "Drafting needs an Anthropic key" in page and "High thinking</button>" not in page
    ask = {"csrf_token": token(page), "kind": "faq_entry", "preset": "standard"}
    asked = client.post(f"{page_url}/drafts", data=ask)
    assert asked.headers["location"] == f"{page_url}?draft=unavailable"
    with committing_engine.connect() as conn:
        calls = table("llm_calls")
        drafting = (calls.c.user_id == owner) & (calls.c.task == "draft_response")
        assert conn.execute(select(calls.c.id).where(drafting)).first() is None  # no call made
