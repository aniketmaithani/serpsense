"""Settings → AI over HTTP: the preset and per-task choices, saved as versions, explained when
a model refuses them, and the user's own."""

import re

import pytest
from sqlalchemy import Engine

from serpsense.domain.enums import LlmTask
from serpsense.entrypoints.web.ai_settings import TASK_NAMES, AiForm
from tests.integration.test_login_pages import browser, token
from tests.integration.test_overview_pages import signed_in

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

# Each task's effort select and the option it shows selected.
SHOWN_EFFORT = re.compile(
    r'name="(effort_[a-z_]+)">\s*(?:<option[^>]*>[^<]*</option>\s*)*?'
    r'<option value="([a-z]+)" selected'
)


def test_the_user_sets_a_preset_and_a_task_of_their_own(committing_engine: Engine) -> None:
    client, _ = signed_in(committing_engine)
    page = client.get("/settings/ai")
    assert page.status_code == 200 and "Draft a response" in page.text and "(preset)" in page.text
    form = {"csrf_token": token(page.text), "preset": "high_thinking"}
    choice = {"model_label_mentions": "claude-sonnet-5-5", "effort_label_mentions": "low"}

    assert client.post("/settings/ai", data={**form, "csrf_token": "forged"}).status_code == 403
    saved = client.post("/settings/ai", data={**form, **choice})
    assert saved.headers["location"] == "/settings/ai?saved=saved"
    shown = client.get(saved.headers["location"]).text
    assert (
        "Saved" in shown
        and "Sonnet 5.5, low" in shown
        and 'value="high_thinking" selected' in shown
    )
    again = client.post("/settings/ai", data={**form, **choice})
    assert again.headers["location"] == "/settings/ai?saved=unchanged"

    refused = {"model_label_mentions": "claude-haiku-4-5", "effort_label_mentions": "max"}
    response = client.post("/settings/ai", data={**form, **refused})
    assert response.status_code == 400 and "Not saved" in response.text
    assert "Sonnet 5.5, low" in response.text  # the saved choice stands

    other, _ = signed_in(committing_engine)
    assert "Sonnet 5.5, low" not in other.get("/settings/ai").text  # each user's own
    assert browser(committing_engine).get("/settings/ai").headers["location"] == "/login"


def test_effort_alone_pins_the_presets_model_and_a_new_preset_alone_pins_nothing(
    committing_engine: Engine,
) -> None:
    client, _ = signed_in(committing_engine)
    page = client.get("/settings/ai").text
    shown = dict(SHOWN_EFFORT.findall(page))
    assert len(shown) == len(LlmTask)
    form = {"csrf_token": token(page), "preset": "maximum", **shown}  # the page as shown
    assert client.post("/settings/ai", data=form).headers["location"].endswith("saved=saved")
    assert (
        "(preset)" in client.get("/settings/ai").text
        and "Opus 5.5, max" in client.get("/settings/ai").text
    )  # every task follows the new preset; nothing was pinned
    deeper = {**form, "effort_label_mentions": "xhigh"}
    client.post("/settings/ai", data=deeper)
    assert "Opus 5.5, High thinking" in client.get("/settings/ai").text


def test_hostile_values_are_refused_without_being_echoed_and_users_stay_apart(
    committing_engine: Engine,
) -> None:
    mine, _ = signed_in(committing_engine)
    theirs, _ = signed_in(committing_engine)
    form = {"csrf_token": token(theirs.get("/settings/ai").text), "preset": "fast"}
    hostile = {"model_label_mentions": "<b>zzz</b>", "effort_label_mentions": "<i>yyy</i>"}
    refused = theirs.post("/settings/ai", data={**form, **hostile})
    assert refused.status_code == 400 and "zzz" not in refused.text and "yyy" not in refused.text
    theirs.post("/settings/ai", data={**form, "model_draft_response": "claude-haiku-4-5",
                                       "effort_draft_response": "low"})  # fmt: skip
    assert "Haiku 4.5" in theirs.get("/settings/ai").text
    assert "Haiku 4.5, low" not in mine.get("/settings/ai").text  # only their own changed


def test_every_task_has_a_name_and_a_form_field() -> None:
    assert set(TASK_NAMES) == set(LlmTask)
    fields = AiForm.model_fields
    assert all(f"model_{t.value}" in fields and f"effort_{t.value}" in fields for t in LlmTask)
