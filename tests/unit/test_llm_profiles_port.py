"""The LLM profiles port, as the scan service's tests fake it."""

import uuid

import pytest

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import OPUS, Effort, TaskSettings
from serpsense.ports.llm_profiles import LlmProfiles
from tests.fakes import FixedProfiles

pytestmark = pytest.mark.unit


def test_a_profile_gives_each_users_task_its_settings_and_keeps_who_asked() -> None:
    settings, owner = TaskSettings(OPUS, Effort.LOW, 8000), uuid.uuid4()
    fixed = FixedProfiles(settings)
    profiles: LlmProfiles = fixed
    assert profiles.settings(owner, LlmTask.LABEL_MENTIONS) == settings
    assert fixed.asked == [(owner, LlmTask.LABEL_MENTIONS)]
