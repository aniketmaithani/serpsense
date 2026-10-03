"""A user's model settings per task: the preset's unless chosen, and only what the model takes."""

import pytest
from pydantic import ValidationError

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import (
    HAIKU,
    MAX_TOKENS,
    MODELS,
    SONNET,
    Effort,
    LlmPreset,
    TaskSettings,
    preset_settings,
)
from serpsense.domain.settings.llm import MODEL_IDS, LlmProfile, TaskChoice, from_stored

pytestmark = pytest.mark.unit


def test_a_task_follows_the_preset_unless_the_user_chose_for_it() -> None:
    choice = TaskChoice(model=SONNET, effort=Effort.MEDIUM)  # type: ignore[arg-type]  # a Literal
    profile = LlmProfile(preset=LlmPreset.FAST, tasks={LlmTask.LABEL_MENTIONS: choice})
    label = profile.settings(LlmTask.LABEL_MENTIONS)
    assert label == TaskSettings(SONNET, Effort.MEDIUM, MAX_TOKENS[LlmTask.LABEL_MENTIONS])
    draft = profile.settings(LlmTask.DRAFT_RESPONSE)
    assert draft == preset_settings(LlmPreset.FAST, LlmTask.DRAFT_RESPONSE)


def test_a_choice_the_model_refuses_is_not_a_profile() -> None:
    too_deep = {"model": HAIKU, "effort": "xhigh"}  # a 16k thinking budget over 8k tokens
    with pytest.raises(ValidationError, match="label_mentions"):
        LlmProfile.model_validate({"tasks": {"label_mentions": too_deep}})
    with pytest.raises(ValidationError):
        LlmProfile.model_validate(
            {"tasks": {"label_mentions": {"model": "gpt-5", "effort": "low"}}}
        )
    with pytest.raises(ValidationError):
        LlmProfile.model_validate({"preset": "balanced", "temperature": 0.2})  # unknown keys


def test_a_stored_profile_keeps_what_still_validates() -> None:
    stored = {
        "preset": "fast",
        "tasks": {
            "label_mentions": {"model": SONNET, "effort": "low"},
            "draft_response": {"model": "claude-retired-1", "effort": "max"},
        },
    }
    profile, dropped = from_stored(stored, LlmPreset.BALANCED)
    assert profile.preset is LlmPreset.FAST and dropped == ("draft_response",)
    assert profile.settings(LlmTask.LABEL_MENTIONS).model == SONNET
    assert profile.settings(LlmTask.DRAFT_RESPONSE) == preset_settings(
        LlmPreset.FAST, LlmTask.DRAFT_RESPONSE
    )
    gone, dropped = from_stored({"preset": "retired"}, LlmPreset.BALANCED)
    assert gone.preset is LlmPreset.BALANCED and dropped == ("preset",)


def test_the_models_a_profile_names_are_the_models_the_capabilities_know() -> None:
    assert set(MODEL_IDS) == set(MODELS)
