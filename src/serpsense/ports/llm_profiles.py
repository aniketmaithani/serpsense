"""Ports for a user's model settings per LLM task (BUILD_PLAN §7.2, ADR-0008): what a call
uses, and the saved profile the AI settings page edits."""

import uuid
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import TaskSettings
from serpsense.domain.settings.llm import LlmProfile


class LlmProfiles(Protocol):
    def settings(self, user_id: uuid.UUID, task: LlmTask) -> TaskSettings:
        """The settings the user's profile gives the task, or its preset's."""
        ...


class LlmProfileStore(Protocol):
    def profile(self, user_id: uuid.UUID) -> LlmProfile | None:
        """The user's latest saved profile; None when they never saved one."""
        ...

    def save(self, user_id: uuid.UUID, profile: LlmProfile, *, at: datetime) -> bool:
        """A new profile version, unless it equals the latest; True when one was written."""
        ...
