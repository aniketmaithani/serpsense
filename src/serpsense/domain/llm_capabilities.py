"""What each Claude model accepts, and the effort presets (ADR-0008, BUILD_PLAN §7).

Settings are checked here before any call, and `request_shape` decides how they are sent, so the
Anthropic adapter only translates and the UI, the gateway and the tests share one set of rules:

- Opus 5.5: effort low…max, always sent; thinking always adaptive; no temperature.
- Sonnet 5.5: effort low…max; thinking adaptive, or off (between_tools) at effort ≤ high; no
  temperature.
- Haiku 4.5: no effort; it maps to a thinking budget (off, 2k, 8k, 16k, 32k tokens); a
  temperature only with thinking off.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from serpsense.domain.enums import LlmTask

OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"
MODELS = (OPUS, SONNET, HAIKU)
MIN_THINKING_BUDGET = 1024


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"  # "High thinking"
    MAX = "max"

    @property
    def rank(self) -> int:
        return list(Effort).index(self)


class LlmPreset(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    HIGH_THINKING = "high_thinking"
    MAXIMUM = "maximum"


_E, _T = Effort, LlmTask
# BUILD_PLAN §7.3: label / classify / assess, then group / explain, then draft.
_COLUMNS = {
    LlmPreset.FAST: (_E.LOW, _E.LOW, _E.MEDIUM),
    LlmPreset.BALANCED: (_E.LOW, _E.MEDIUM, _E.HIGH),
    LlmPreset.HIGH_THINKING: (_E.MEDIUM, _E.HIGH, _E.XHIGH),
    LlmPreset.MAXIMUM: (_E.HIGH, _E.XHIGH, _E.MAX),
}
_TASK_COLUMN = {
    _T.LABEL_MENTIONS: 0,
    _T.CLASSIFY_AUTOCOMPLETE: 0,
    _T.ASSESS_AI_OVERVIEW: 0,
    _T.GROUP_NARRATIVES: 1,
    _T.EXPLAIN_CRISIS: 1,
    _T.DRAFT_RESPONSE: 2,
}
PRESETS: Mapping[LlmPreset, Mapping[LlmTask, Effort]] = {
    preset: {task: efforts[column] for task, column in _TASK_COLUMN.items()}
    for preset, efforts in _COLUMNS.items()
}
HAIKU_BUDGETS: Mapping[Effort, int | None] = {
    _E.LOW: None,  # thinking off
    _E.MEDIUM: 2048,
    _E.HIGH: 8192,
    _E.XHIGH: 16384,
    _E.MAX: 32768,
}


# BUILD_PLAN §7.1: every task defaults to Opus 5.5, with room for its longest answer.
MAX_TOKENS: Mapping[LlmTask, int] = {
    _T.LABEL_MENTIONS: 8000,  # 25 labels with reasons, and low-effort thinking
    _T.CLASSIFY_AUTOCOMPLETE: 4000,
    _T.ASSESS_AI_OVERVIEW: 4000,
    _T.GROUP_NARRATIVES: 8000,
    _T.EXPLAIN_CRISIS: 4000,
    _T.DRAFT_RESPONSE: 16000,
}


class UnsupportedSetting(ValueError):
    """A combination the chosen model rejects."""


@dataclass(frozen=True)
class TaskSettings:
    model: str
    effort: Effort
    max_tokens: int
    # None: the model's own default (Opus and Sonnet think adaptively, Haiku follows its
    # effort); False turns Sonnet's off at effort ≤ high.
    thinking: bool | None = None
    temperature: float | None = None  # Haiku with thinking off only


@dataclass(frozen=True)
class RequestShape:
    """How the settings are sent: the adapter maps these fields onto the SDK, nothing more."""

    model: str
    max_tokens: int
    effort: Effort | None  # None: not sent (Haiku)
    thinking: Literal["adaptive", "between_tools", "off"] | int  # an int is a token budget
    temperature: float | None


def request_shape(settings: TaskSettings) -> RequestShape:
    """Check the settings against the model and return how to send them."""
    if settings.model not in MODELS:
        raise UnsupportedSetting(f"unknown model {settings.model}")
    if settings.max_tokens < 1:
        raise UnsupportedSetting("max_tokens must be positive")
    if settings.model == HAIKU:
        return _haiku(settings)
    if settings.temperature is not None:
        raise UnsupportedSetting(f"{settings.model} rejects a temperature")
    if settings.thinking is not False:
        thinking: Literal["adaptive", "between_tools"] = "adaptive"
    elif settings.model == SONNET and settings.effort.rank <= Effort.HIGH.rank:
        thinking = "between_tools"
    else:
        raise UnsupportedSetting(f"{settings.model} can't turn thinking off at this effort")
    return RequestShape(settings.model, settings.max_tokens, settings.effort, thinking, None)


def _haiku(settings: TaskSettings) -> RequestShape:
    budget = HAIKU_BUDGETS[settings.effort]
    if settings.thinking is not None and settings.thinking != (budget is not None):
        raise UnsupportedSetting("Haiku thinks exactly at medium effort and above")
    if budget is not None and not MIN_THINKING_BUDGET <= budget < settings.max_tokens:
        raise UnsupportedSetting("Haiku's thinking budget must stay below max_tokens")
    if settings.temperature is not None and (
        budget is not None or not 0 <= settings.temperature <= 1
    ):
        raise UnsupportedSetting("Haiku takes a temperature of 0-1, with thinking off only")
    thinking: Literal["off"] | int = budget if budget is not None else "off"
    return RequestShape(HAIKU, settings.max_tokens, None, thinking, settings.temperature)


def preset_settings(preset: LlmPreset, task: LlmTask) -> TaskSettings:
    """A task's settings under a preset, before a user changes any of them."""
    return TaskSettings(OPUS, PRESETS[preset][task], MAX_TOKENS[task])
