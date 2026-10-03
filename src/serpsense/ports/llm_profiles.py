"""Port for a user's model settings per LLM task (BUILD_PLAN §7.2, ADR-0008)."""

import uuid
from typing import Protocol

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import TaskSettings


class LlmProfiles(Protocol):
    def settings(self, user_id: uuid.UUID, task: LlmTask) -> TaskSettings:
        """The settings the user's profile gives the task, or its preset's."""
        ...
