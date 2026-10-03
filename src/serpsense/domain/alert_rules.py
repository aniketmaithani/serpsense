"""Deterministic alert rules (BUILD_PLAN §12). The model never decides whether to alert; it only
explains an alert that a rule raised (ADR-0008).

A scan raises an alert when its brand's crisis level went up since the brand's previous scored
scan, or when a negative suggestion first appeared in the brand's autocomplete. Narrative alerts
come with narrative grouping. A brand that is still warming up (its crisis has no level yet)
raises none, and the first level after the warm-up is where the brand starts, not a rise.

**Cooldown:** a rule that fired for the brand within 12 hours stays quiet, counted between the
scans' creation times with 30 minutes' slack, so the next scan of a 12-hourly schedule isn't held
back by the one before. A level that rises past the one last alerted still alerts.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType

from serpsense.domain.enums import AlertRule, CrisisLevel

COOLDOWN, SLACK = timedelta(hours=12), timedelta(minutes=30)


@dataclass(frozen=True)
class Fired:
    """The brand's latest alert under a rule: when its scan was made, and that scan's level."""

    at: datetime
    level: CrisisLevel | None


@dataclass(frozen=True)
class AlertFacts:
    at: datetime  # when the scan was made
    level: CrisisLevel | None  # the scan's crisis level; none while the brand warms up
    previous: CrisisLevel | None  # the level of the brand's previous scored scan
    autocomplete: int  # the scan's new-negative-autocomplete crisis component, 0-100
    last: Mapping[AlertRule, Fired] = field(default_factory=lambda: MappingProxyType({}))


def due(facts: AlertFacts) -> list[AlertRule]:
    """The rules this scan fires, in a fixed order."""
    if facts.level is None:
        return []
    rose = facts.previous is not None and facts.level.rank > facts.previous.rank
    fired = [AlertRule.LEVEL_INCREASE] if rose and not _held(facts, facts.level) else []
    if facts.autocomplete > 0 and not _cooling(facts, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE):
        fired.append(AlertRule.NEW_NEGATIVE_AUTOCOMPLETE)
    return fired


def _held(facts: AlertFacts, level: CrisisLevel) -> bool:
    """A level increase within the cooldown alerts only above the level it last alerted."""
    last = facts.last.get(AlertRule.LEVEL_INCREASE)
    if last is None or not _within_cooldown(facts, last):
        return False
    return last.level is not None and level.rank <= last.level.rank


def _cooling(facts: AlertFacts, rule: AlertRule) -> bool:
    last = facts.last.get(rule)
    return last is not None and _within_cooldown(facts, last)


def _within_cooldown(facts: AlertFacts, last: Fired) -> bool:
    return facts.at - last.at < COOLDOWN - SLACK
