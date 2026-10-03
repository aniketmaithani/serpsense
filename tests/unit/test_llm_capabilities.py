"""Per-model rules and presets for model calls (ADR-0008, BUILD_PLAN §7)."""

import pytest

from serpsense.config import LlmPreset as ConfigPreset
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import (
    HAIKU,
    MAX_TOKENS,
    OPUS,
    PRESETS,
    SONNET,
    Effort,
    LlmPreset,
    RequestShape,
    TaskSettings,
    UnsupportedSetting,
    preset_settings,
    request_shape,
)

pytestmark = pytest.mark.unit

E = Effort


def test_every_preset_sets_every_task_and_high_thinking_drafts_at_xhigh() -> None:
    assert ConfigPreset is LlmPreset  # one enum, owned by the domain
    assert all(set(efforts) == set(LlmTask) for efforts in PRESETS.values())
    balanced, high = PRESETS[LlmPreset.BALANCED], PRESETS[LlmPreset.HIGH_THINKING]
    assert (balanced[LlmTask.LABEL_MENTIONS], balanced[LlmTask.DRAFT_RESPONSE]) == (E.LOW, E.HIGH)
    assert high[LlmTask.DRAFT_RESPONSE] is E.XHIGH
    assert PRESETS[LlmPreset.MAXIMUM][LlmTask.GROUP_NARRATIVES] is E.XHIGH


@pytest.mark.parametrize(
    ("settings", "shape"),
    [
        (TaskSettings(OPUS, E.LOW, 4096), RequestShape(OPUS, 4096, E.LOW, "adaptive", None)),
        (TaskSettings(OPUS, E.MAX, 64000), RequestShape(OPUS, 64000, E.MAX, "adaptive", None)),
        (
            TaskSettings(SONNET, E.HIGH, 4096, thinking=False),
            RequestShape(SONNET, 4096, E.HIGH, "between_tools", None),
        ),
        (
            TaskSettings(HAIKU, E.LOW, 1024, thinking=False, temperature=0.2),
            RequestShape(HAIKU, 1024, None, "off", 0.2),
        ),
        (TaskSettings(HAIKU, E.LOW, 1024), RequestShape(HAIKU, 1024, None, "off", None)),
        (TaskSettings(HAIKU, E.HIGH, 16000), RequestShape(HAIKU, 16000, None, 8192, None)),
    ],
)
def test_supported_settings_and_how_they_are_sent(
    settings: TaskSettings, shape: RequestShape
) -> None:
    assert request_shape(settings) == shape


@pytest.mark.parametrize(
    "settings",
    [
        TaskSettings("claude-3-opus", E.LOW, 4096),  # not offered
        TaskSettings(OPUS, E.LOW, 0),
        TaskSettings(OPUS, E.LOW, 4096, thinking=False),  # Opus always thinks
        TaskSettings(OPUS, E.LOW, 4096, temperature=0.5),
        TaskSettings(SONNET, E.XHIGH, 4096, thinking=False),  # off only up to high
        TaskSettings(SONNET, E.LOW, 4096, temperature=1.0),
        TaskSettings(HAIKU, E.LOW, 4096, thinking=True),  # low effort is thinking off
        TaskSettings(HAIKU, E.MEDIUM, 4096, thinking=False),
        TaskSettings(HAIKU, E.MAX, 32768),  # the budget must stay below max_tokens
        TaskSettings(HAIKU, E.MEDIUM, 4096, temperature=0.2),  # thinking on: no temperature
        TaskSettings(HAIKU, E.LOW, 4096, thinking=False, temperature=1.5),
    ],
)
def test_unsupported_settings_are_refused_before_any_call(settings: TaskSettings) -> None:
    with pytest.raises(UnsupportedSetting):
        request_shape(settings)


def test_haiku_budgets_follow_its_effort() -> None:
    budgets = [request_shape(TaskSettings(HAIKU, e, 40000)).thinking for e in list(E)[1:]]
    assert budgets == [2048, 8192, 16384, 32768]


def test_every_task_has_preset_settings_on_opus_with_room_for_its_answer() -> None:
    for preset in LlmPreset:
        for task in LlmTask:
            settings = preset_settings(preset, task)
            assert (settings.model, settings.effort) == (OPUS, PRESETS[preset][task])
            assert request_shape(settings).max_tokens == MAX_TOKENS[task] >= 4000
    labelling = preset_settings(LlmPreset.BALANCED, LlmTask.LABEL_MENTIONS)
    assert (labelling.effort, labelling.max_tokens) == (Effort.LOW, 8000)
