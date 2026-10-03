"""The deterministic alert rules: a rising level, a new negative suggestion, a spreading story,
and the cooldown."""

import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from serpsense.domain.alert_rules import AlertFacts, Fired, Story, due, spreading
from serpsense.domain.enums import AlertRule, CrisisLevel, MentionSource

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
LOW, MEDIUM, HIGH = CrisisLevel.LOW, CrisisLevel.MEDIUM, CrisisLevel.HIGH
RISE, SUGGESTION = AlertRule.LEVEL_INCREASE, AlertRule.NEW_NEGATIVE_AUTOCOMPLETE


def facts(
    level: CrisisLevel | None,
    previous: CrisisLevel | None = LOW,
    *,
    autocomplete: int = 0,
    last: Mapping[AlertRule, Fired] | None = None,
) -> AlertFacts:
    return AlertFacts(AT, level, previous, autocomplete, MappingProxyType(dict(last or {})))


@pytest.mark.parametrize(
    ("level", "previous", "fires"),
    [
        (MEDIUM, LOW, True),
        (HIGH, MEDIUM, True),
        (MEDIUM, None, False),  # the first level after the warm-up: where it starts
        (LOW, None, False),
        (MEDIUM, MEDIUM, False),
        (LOW, HIGH, False),  # easing is not an alert
        (None, LOW, False),  # warming up: no level, no alert
    ],
)
def test_a_level_that_rises_alerts(
    level: CrisisLevel | None, previous: CrisisLevel | None, fires: bool
) -> None:
    assert (due(facts(level, previous)) == [RISE]) is fires


def test_a_new_negative_suggestion_alerts_once_the_brand_has_a_level() -> None:
    assert due(facts(LOW, autocomplete=80)) == [SUGGESTION]
    assert due(facts(HIGH, MEDIUM, autocomplete=100)) == [RISE, SUGGESTION]
    assert due(facts(None, autocomplete=100)) == []  # warming up


def test_a_rule_that_fired_lately_cools_down() -> None:
    lately = {SUGGESTION: Fired(AT - timedelta(hours=2), LOW)}
    assert due(facts(LOW, autocomplete=80, last=lately)) == []
    schedule = {SUGGESTION: Fired(AT - timedelta(hours=11, minutes=55), LOW)}  # 12-hourly
    assert due(facts(LOW, autocomplete=80, last=schedule)) == [SUGGESTION]


def test_a_level_rising_past_the_last_alert_alerts_within_the_cooldown() -> None:
    alerted = {RISE: Fired(AT - timedelta(hours=1), MEDIUM)}
    assert due(facts(HIGH, MEDIUM, last=alerted)) == [RISE]
    flapped = {RISE: Fired(AT - timedelta(hours=1), HIGH)}
    assert due(facts(HIGH, MEDIUM, last=flapped)) == []  # back to the level it alerted
    earlier = {RISE: Fired(AT - timedelta(hours=13), HIGH)}
    assert due(facts(HIGH, MEDIUM, last=earlier)) == [RISE]  # cooled down


def story(last: datetime | None = None, **sources: int) -> Story:
    counts = {MentionSource(source): count for source, count in sources.items()}
    return Story(uuid.uuid4(), "Drivers demand cash", MappingProxyType(counts), last)


@pytest.mark.parametrize(
    ("sources", "spreads"),
    [
        ({"play_review": 3, "news": 2}, True),
        ({"play_review": 2, "news": 1, "autocomplete": 1}, False),  # four mentions
        ({"play_review": 9}, False),  # one surface
        (
            {"serp_result": 2, "top_story": 2, "people_also_ask": 1},
            False,
        ),  # all on the results page
        ({"play_review": 4, "maps_review": 0, "news": 1}, True),
    ],
)
def test_a_story_spreads_with_5_mentions_on_2_surfaces(
    sources: dict[str, int], spreads: bool
) -> None:
    grown = story(**sources)
    assert (spreading(replace(facts(LOW), stories=(grown,))) == [grown]) is spreads


def test_a_spreading_story_cools_down_on_its_own_and_waits_for_the_warm_up() -> None:
    lately = story(AT - timedelta(hours=2), play_review=4, news=1)
    schedule = story(AT - timedelta(hours=11, minutes=55), play_review=4, news=1)
    fresh = story(play_review=4, news=1)
    stories = (lately, schedule, fresh)
    assert spreading(replace(facts(LOW), stories=stories)) == [schedule, fresh]
    assert spreading(replace(facts(None), stories=stories)) == []  # warming up
