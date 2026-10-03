"""The AI settings page (BUILD_PLAN §7.2, §13): a user's preset and the tasks they changed.

A task left to the preset follows it when the preset changes; a task the user set keeps its
model and effort. Changing only a task's effort keeps the preset's model at that effort. Every
choice is checked against what its model accepts before it is saved (ADR-0008), and a save that
changes nothing writes nothing.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import LlmPreset, TaskSettings, preset_settings
from serpsense.domain.settings.llm import LlmProfile, TaskChoice
from serpsense.ports.clock import Clock
from serpsense.ports.llm_profiles import LlmProfileStore


class Saved(StrEnum):
    SAVED = "saved"
    UNCHANGED = "unchanged"
    INVALID = "invalid"


@dataclass(frozen=True)
class Outcome:
    saved: Saved
    reason: str | None = None  # why a profile was refused, in our own words


@dataclass(frozen=True)
class TaskRow:
    task: LlmTask
    settings: TaskSettings
    chosen: bool  # the user set it; otherwise the preset decides


@dataclass(frozen=True)
class AiView:
    preset: LlmPreset
    rows: tuple[TaskRow, ...]


class AiSettings:
    def __init__(self, store: LlmProfileStore, clock: Clock, *, fallback: LlmPreset) -> None:
        self._store, self._clock, self._fallback = store, clock, fallback

    def view(self, user_id: uuid.UUID) -> AiView:
        profile = self._store.profile(user_id)
        if profile is None:
            profile = LlmProfile(preset=self._fallback)
        rows = tuple(
            TaskRow(task, profile.settings(task), task in profile.tasks) for task in LlmTask
        )
        return AiView(profile.preset, rows)

    def save(
        self,
        user_id: uuid.UUID,
        preset: LlmPreset,
        picked: Mapping[LlmTask, tuple[str, str]],
    ) -> Outcome:
        """Save the preset and, per task, the (model, effort) picked; an empty model means the
        preset's. What the models refuse is not saved, and the outcome says why."""
        shown = self._store.profile(user_id)
        if shown is None:
            shown = LlmProfile(preset=self._fallback)
        try:
            tasks = {
                task: TaskChoice.model_validate({"model": model, "effort": effort})
                for task, (model, effort) in _choices(shown, preset, picked).items()
            }
            profile = LlmProfile(preset=preset, tasks=tasks)
        except ValidationError as exc:
            return Outcome(Saved.INVALID, _reason(exc))
        written = self._store.save(user_id, profile, at=self._clock.now())
        return Outcome(Saved.SAVED if written else Saved.UNCHANGED)


def _choices(
    shown: LlmProfile, preset: LlmPreset, picked: Mapping[LlmTask, tuple[str, str]]
) -> dict[LlmTask, tuple[str, str]]:
    """The tasks set by hand: a model picked, or the preset's model at an effort other than the
    one the page showed (so changing only the preset pins nothing)."""
    chosen: dict[LlmTask, tuple[str, str]] = {}
    for task, (model, effort) in picked.items():
        if model:
            chosen[task] = (model, effort)
        elif effort and effort != shown.settings(task).effort:
            chosen[task] = (preset_settings(preset, task).model, effort)
    return chosen


def _reason(exc: ValidationError) -> str:
    """The first problem in words a person can act on (our own messages, never input)."""
    error = exc.errors()[0]
    message = str(error.get("msg", "not a supported combination"))
    return message.removeprefix("Value error, ")
