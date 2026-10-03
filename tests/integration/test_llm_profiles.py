"""Users' model settings on Postgres: the latest version, the preset without one, and a stored
task that no longer validates falling back to the preset alone."""

import uuid

import pytest
from sqlalchemy import Engine
from structlog.testing import capture_logs

from serpsense.adapters.db.llm_profiles import SqlLlmProfiles
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import SONNET, Effort, LlmPreset, preset_settings
from serpsense.domain.settings.llm import LlmProfile, TaskChoice
from tests.integration.db_helpers import NOW, add, add_user, table

pytestmark = pytest.mark.integration

LABEL = LlmTask.LABEL_MENTIONS


def test_the_latest_profile_wins_and_a_user_without_one_gets_the_preset(
    committing_engine: Engine,
) -> None:
    profiles = SqlLlmProfiles(committing_engine, LlmPreset.HIGH_THINKING)
    with committing_engine.begin() as conn:
        user = add_user(conn, f"{uuid.uuid4().hex[:8]}@example.com")
    assert profiles.profile(user) is None
    assert profiles.settings(user, LABEL) == preset_settings(LlmPreset.HIGH_THINKING, LABEL)

    choice = TaskChoice(model=SONNET, effort=Effort.LOW)  # type: ignore[arg-type]  # a Literal
    mine = LlmProfile(preset=LlmPreset.FAST, tasks={LABEL: choice})
    assert profiles.save(user, mine, at=NOW) is True
    assert profiles.save(user, mine, at=NOW) is False  # the same again: nothing written
    assert profiles.profile(user) == mine
    assert profiles.settings(user, LABEL).model == SONNET


def test_a_stored_task_that_no_longer_validates_falls_back_to_the_preset_alone(
    committing_engine: Engine,
) -> None:
    profiles = SqlLlmProfiles(committing_engine, LlmPreset.BALANCED)
    with committing_engine.begin() as conn:
        user = add_user(conn, f"{uuid.uuid4().hex[:8]}@example.com")
        tasks = {
            "label_mentions": {"model": SONNET, "effort": "low"},
            "draft_response": {"model": "claude-gone-1", "effort": "low"},
        }
        add(conn, table("user_llm_profile_versions"), user_id=user, schema_version=1,
            document={"preset": "fast", "tasks": tasks}, created_at=NOW)  # fmt: skip
    with capture_logs() as logs:
        profile = profiles.profile(user)
    assert profile is not None and profile.preset is LlmPreset.FAST
    assert profiles.settings(user, LABEL).model == SONNET  # the valid choice stands
    draft = profiles.settings(user, LlmTask.DRAFT_RESPONSE)
    assert draft == preset_settings(LlmPreset.FAST, LlmTask.DRAFT_RESPONSE)
    assert [entry["dropped"] for entry in logs if entry["event"] == "llm_profile.invalid"] == [
        ["draft_response"]
    ]
