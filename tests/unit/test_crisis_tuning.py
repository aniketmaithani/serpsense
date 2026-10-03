"""A brand's crisis tuning: its knobs' ranges, and the levels and alerts it changes."""

import uuid
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from serpsense.domain.alert_rules import AlertFacts, Fired, Story, due, spreading
from serpsense.domain.enums import AlertRule, CrisisLevel, MentionSource
from serpsense.domain.scoring.crisis import LEVEL_FLOORS, WARM_UP_SCANS, has_level, level
from serpsense.domain.scoring.tuning import DEFAULT_TUNING, RANGES, CrisisTuning, InvalidTuning

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
LOW, MEDIUM, HIGH = CrisisLevel.LOW, CrisisLevel.MEDIUM, CrisisLevel.HIGH


def test_the_defaults_are_the_scoring_versions() -> None:
    assert DEFAULT_TUNING.floors() == LEVEL_FLOORS == ((HIGH, 70), (MEDIUM, 40), (LOW, 0))
    assert DEFAULT_TUNING.warm_up_scans == WARM_UP_SCANS == 3
    assert DEFAULT_TUNING.cooldown == timedelta(hours=12)
    assert {knob.name for knob in fields(CrisisTuning)} == set(RANGES)


@pytest.mark.parametrize("knob", sorted(RANGES))
def test_each_knob_is_refused_outside_its_range(knob: str) -> None:
    low, high = RANGES[knob]
    for wrong in (low - 1, high + 1):
        with pytest.raises(InvalidTuning):
            replace(DEFAULT_TUNING, **{knob: wrong})


def test_high_starts_above_medium() -> None:
    assert CrisisTuning(medium_at=30, high_at=31).high_at == 31
    for high in (30, 29):
        with pytest.raises(InvalidTuning):
            CrisisTuning(medium_at=30, high_at=high)


def test_levels_and_the_warm_up_follow_the_tuning() -> None:
    touchy = CrisisTuning(warm_up_scans=0, medium_at=20, high_at=50)
    assert [level(s, touchy) for s in (0, 19, 20, 49, 50)] == [LOW, LOW, MEDIUM, MEDIUM, HIGH]
    assert [level(s) for s in (39, 40, 69, 70)] == [LOW, MEDIUM, MEDIUM, HIGH]  # the defaults
    assert has_level(0, touchy) and not has_level(2) and has_level(3)


def test_the_cooldown_follows_the_tuning() -> None:
    last = MappingProxyType(
        {AlertRule.NEW_NEGATIVE_AUTOCOMPLETE: Fired(AT - timedelta(hours=3), LOW)}
    )
    quiet = AlertFacts(AT, LOW, LOW, 90, last)
    assert due(quiet) == []  # 3 hours into the default 12
    brisk = replace(quiet, tuning=CrisisTuning(cooldown_hours=2))
    assert due(brisk) == [AlertRule.NEW_NEGATIVE_AUTOCOMPLETE]


def test_what_counts_as_spreading_follows_the_tuning() -> None:
    sources = MappingProxyType({MentionSource.NEWS: 2, MentionSource.PLAY_REVIEW: 1})
    story = Story(uuid.uuid4(), "Fares", sources)
    facts = AlertFacts(AT, LOW, LOW, 0, MappingProxyType({}), (story,))
    assert spreading(facts) == []  # 3 mentions: under the default 5
    assert spreading(replace(facts, tuning=CrisisTuning(spread_mentions=3))) == [story]
    strict = CrisisTuning(spread_mentions=3, spread_surfaces=3)
    assert spreading(replace(facts, tuning=strict)) == []  # on two surfaces, not three
