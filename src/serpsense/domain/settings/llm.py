"""A user's model settings per task (BUILD_PLAN §7.2, ADR-0008): a preset, and the tasks they
changed. Read whole from `user_llm_profile_versions`; unknown keys are refused, and every task's
choice is checked against what its model accepts before it is saved or used. A stored profile is
read leniently (`from_stored`): a task whose choice no longer validates (a model since dropped)
falls back to the preset alone, and the rest of the profile stands.
"""

from collections.abc import Mapping
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import (
    MAX_TOKENS,
    Effort,
    LlmPreset,
    TaskSettings,
    UnsupportedSetting,
    preset_settings,
    request_shape,
)

SCHEMA_VERSION = 1


ModelId = Literal["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"]  # = MODELS
MODEL_IDS: tuple[str, ...] = get_args(ModelId)


class TaskChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: ModelId
    effort: Effort


class LlmProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    preset: LlmPreset = LlmPreset.BALANCED
    tasks: dict[LlmTask, TaskChoice] = {}

    @model_validator(mode="after")
    def _accepted(self) -> "LlmProfile":
        for task in self.tasks:
            try:
                request_shape(self.settings(task))
            except UnsupportedSetting as exc:
                raise ValueError(f"{task.value}: {exc}") from exc
        return self

    def settings(self, task: LlmTask) -> TaskSettings:
        """The task's settings: the user's choice, or the preset's."""
        choice = self.tasks.get(task)
        if choice is None:
            return preset_settings(self.preset, task)
        return TaskSettings(choice.model, choice.effort, MAX_TOKENS[task])


def from_stored(
    document: Mapping[str, Any], fallback: LlmPreset
) -> tuple[LlmProfile, tuple[str, ...]]:
    """A stored profile and the tasks dropped from it because they no longer validate; a preset
    that is no longer one becomes `fallback`."""
    preset = document.get("preset")
    kept: dict[LlmTask, TaskChoice] = {}
    dropped: list[str] = []
    stored = document.get("tasks")
    for name, choice in (stored if isinstance(stored, Mapping) else {}).items():
        try:
            one = LlmProfile.model_validate({"tasks": {name: choice}})
        except ValidationError:
            dropped.append(str(name))
            continue
        kept.update(one.tasks)
    if preset not in set(LlmPreset):
        preset, dropped = fallback, [*dropped, "preset"]
    return LlmProfile(preset=LlmPreset(preset), tasks=kept), tuple(dropped)
