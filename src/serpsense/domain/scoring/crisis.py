"""The crisis score and level: change against the brand's usual (BUILD_PLAN §11, docs/scoring.md).

A crisis is a change, so every component compares a scan with the brand's usual, or counts only
what is new: a brand that always has some negative reviews and articles is not in crisis
because of them. A brand needs a few earlier scans before "new" and "usual" mean anything, so
until then it has no level (and so raises no alert); its components are still recorded.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from serpsense.domain.enums import CrisisComponent, CrisisLevel
from serpsense.domain.scoring.surfaces import ratio

USUAL_SCANS = 8  # a brand's usual is the median of its newest eight earlier scans
USUAL_FLOOR = 2
WARM_UP_SCANS = 3  # earlier scored scans a brand needs before its crisis has a level

CRISIS_WEIGHTS_BP: Mapping[CrisisComponent, int] = {
    CrisisComponent.VELOCITY: 3000,
    CrisisComponent.SPREAD: 2500,
    CrisisComponent.AUTOCOMPLETE: 2000,
    CrisisComponent.TRENDS: 1500,
    CrisisComponent.PRESS: 1000,
}
LEVEL_FLOORS = ((CrisisLevel.HIGH, 70), (CrisisLevel.MEDIUM, 40), (CrisisLevel.LOW, 0))


def usual(earlier: Sequence[int]) -> int:
    """A brand's usual count: the median (the lower middle) of its newest eight earlier scans,
    newest first, and never below 2, so a quiet brand isn't alarmed by its second complaint."""
    recent = sorted(earlier[:USUAL_SCANS])
    return max(USUAL_FLOOR, recent[(len(recent) - 1) // 2]) if recent else USUAL_FLOOR


def velocity(new_negative: int, earlier: Sequence[int]) -> int:
    """Negative mentions first seen in this scan against the usual: 0 at or below it, 100 at
    four times it."""
    base = usual(earlier)
    return 0 if new_negative <= base else min(100, ratio(new_negative - base, 3 * base))


@dataclass(frozen=True)
class SurfaceNegatives:
    """A surface's negative mentions first seen in this scan, and in the earlier scans."""

    new_negative: int
    earlier: Sequence[int] = ()


def spread(surfaces: Sequence[SurfaceNegatives]) -> int:
    """The share of the surfaces that showed something whose new negatives exceed their usual."""
    if not surfaces:
        return 0
    return ratio(sum(s.new_negative > usual(s.earlier) for s in surfaces), len(surfaces))


def has_level(earlier_scans: int) -> bool:
    """On its first scans everything a brand shows is new: its crisis has no level yet."""
    return earlier_scans >= WARM_UP_SCANS


def new_negative_autocomplete(ranks: Sequence[int | None]) -> int:
    """Negative suggestions first seen for the brand in this scan: the highest decides (100, 90
    and 80 for the top three places, 70 below), 0 when there are none."""
    if any(rank is not None and rank < 1 for rank in ranks):
        raise ValueError("a rank starts at 1")
    return max((max(70, 110 - 10 * (rank or 4)) for rank in ranks), default=0)


def rising_negative_trends(count: int) -> int:
    """Negative rising related queries first seen for the brand in this scan."""
    return 0 if count <= 0 else min(100, 60 + 20 * (count - 1))


def press(severities: Sequence[int]) -> int:
    """Negative articles first seen in this scan and published in the last 48 hours: half the
    sum of their severities, capped."""
    if any(not 0 <= severity <= 100 for severity in severities):
        raise ValueError("severity is 0-100")
    return min(100, (sum(severities) + 1) // 2)  # half, rounded half up


def crisis(components: Mapping[CrisisComponent, int]) -> int:
    """The weighted sum of the components (weights in basis points; a missing one is 0)."""
    if any(not 0 <= value <= 100 for value in components.values()):
        raise ValueError("a crisis component is 0-100")
    return ratio(sum(CRISIS_WEIGHTS_BP[c] * v for c, v in components.items()), 1_000_000)


def level(score: int) -> CrisisLevel:
    if not 0 <= score <= 100:
        raise ValueError("a crisis score is 0-100")
    return next(name for name, floor in LEVEL_FLOORS if score >= floor)
