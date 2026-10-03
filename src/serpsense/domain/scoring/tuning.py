"""A brand's crisis tuning: when its crisis gets a level, where the levels start, and how its
alerts are paced (docs/scoring.md). The signals and the crisis score never change with it; the
owner tunes how the score is read. The defaults are scoring version `s1`'s.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import timedelta

from serpsense.domain.enums import CrisisLevel

# Each knob's allowed range, inclusive; the database checks the same.
RANGES: Mapping[str, tuple[int, int]] = {
    "warm_up_scans": (0, 8),
    "medium_at": (1, 99),
    "high_at": (2, 100),
    "cooldown_hours": (1, 72),
    "spread_mentions": (2, 50),
    "spread_surfaces": (1, 5),
}


class InvalidTuning(ValueError):
    """A knob out of its range, or a high level that starts at or below the medium one."""


@dataclass(frozen=True, kw_only=True)
class CrisisTuning:
    warm_up_scans: int = 3  # earlier scored scans a brand needs before its crisis has a level
    medium_at: int = 40  # the crisis score where medium starts
    high_at: int = 70  # and high
    cooldown_hours: int = 12  # how long a rule that fired stays quiet for the brand
    spread_mentions: int = 5  # a story spreading: this many mentions
    spread_surfaces: int = 2  # on this many surfaces

    def __post_init__(self) -> None:
        for knob in fields(self):
            low, high = RANGES[knob.name]
            if not low <= getattr(self, knob.name) <= high:
                raise InvalidTuning(f"{knob.name} is {low}-{high}")
        if self.high_at <= self.medium_at:
            raise InvalidTuning("high starts above medium")

    @property
    def cooldown(self) -> timedelta:
        return timedelta(hours=self.cooldown_hours)

    def floors(self) -> tuple[tuple[CrisisLevel, int], ...]:
        """Each level and the score it starts at, highest first."""
        return (
            (CrisisLevel.HIGH, self.high_at),
            (CrisisLevel.MEDIUM, self.medium_at),
            (CrisisLevel.LOW, 0),
        )


DEFAULT_TUNING = CrisisTuning()
