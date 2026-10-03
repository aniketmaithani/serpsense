"""LLM profiles before users can edit theirs (#97): every user gets the configured preset."""

import uuid

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import LlmPreset, TaskSettings, preset_settings


class PresetProfiles:
    def __init__(self, preset: LlmPreset) -> None:
        self._preset = preset

    def settings(self, user_id: uuid.UUID, task: LlmTask) -> TaskSettings:
        return preset_settings(self._preset, task)
