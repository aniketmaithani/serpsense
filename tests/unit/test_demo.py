"""The demo's settings fit the SerpApi free plan (BUILD_PLAN §22)."""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from serpsense.domain.estimator import BrandFacts, estimate
from serpsense.domain.schedule import Schedule
from serpsense.domain.settings.search import resolve
from serpsense.services.demo import OLA, RIVALS, Seeded, draft_a_reply
from serpsense.services.drafts import DraftResult, Outcome

pytestmark = pytest.mark.unit

ONE_APP = BrandFacts(apps=1, locations=0)
IST = ZoneInfo("Asia/Kolkata")
DEMO_BUDGET = 240  # DEFAULT_MONTHLY_SEARCH_BUDGET for the demo deployment (BUILD_PLAN §22)


def test_ola_makes_at_most_eight_searches_a_scan_twice_a_day() -> None:
    settings = resolve(OLA.settings)
    assert estimate(settings, ONE_APP) == 8
    assert (settings.languages, settings.trends.related_queries) == (("en",), True)
    assert OLA.every.interval_minutes == 12 * 60


def test_each_competitor_makes_three_searches_a_scan_once_a_day() -> None:
    assert [r.name for r in RIVALS] == ["Uber", "Rapido", "Namma Yatri", "inDrive"]
    for rival in RIVALS:
        settings = resolve(rival.settings)
        assert estimate(settings, ONE_APP) == 3
        assert not (settings.trends.enabled or settings.autocomplete.enabled)
        assert rival.every.interval_minutes == 24 * 60


def scans(every: Schedule, start: datetime, end: datetime) -> int:
    """How many slots a brand on this schedule is scanned in, from its slot at `start`."""
    slot, count = every.slot(start), 0
    while slot <= end:
        count, slot = count + 1, slot + timedelta(minutes=every.interval_minutes)
    return count


def test_seeded_tonight_the_demo_stays_under_its_budget_until_the_deadline() -> None:
    tonight = datetime(2026, 10, 3, 18, 0, tzinfo=IST)
    deadline = datetime(2026, 10, 10, 23, 59, tzinfo=IST)
    ola = scans(OLA.every, tonight, deadline) * estimate(resolve(OLA.settings), ONE_APP)
    rivals = sum(
        scans(r.every, tonight, deadline) * estimate(resolve(r.settings), ONE_APP) for r in RIVALS
    )
    assert (ola, rivals) == (15 * 8, 4 * 8 * 3)  # 15 Ola scans and 8 of each competitor
    assert ola + rivals <= DEMO_BUDGET <= 250 - 10  # the free plan, less the fixtures' searches


class NoStories:
    def of_brand(self, user_id: object, brand_id: object, *, limit: int) -> list[object]:
        return []


def test_a_brand_with_no_story_gets_no_draft() -> None:
    seeded = Seeded(uuid.uuid4(), uuid.uuid4(), ())
    assert draft_a_reply(NoStories(), None, seeded) is None  # type: ignore[arg-type]  # never asked


class OneStory:
    def of_brand(self, user_id: object, brand_id: object, *, limit: int) -> list[object]:
        return [SimpleNamespace(narrative_id=uuid.uuid4())]


class Drafted:
    def __init__(self, existing: int) -> None:
        self.existing, self.asked = existing, 0

    def recent(self, user_id: object, *, limit: int) -> list[object]:
        return [object()] * self.existing

    def draft(self, *args: object, **kwargs: object) -> DraftResult:
        self.asked += 1
        return DraftResult(Outcome.DRAFTED)


def test_a_reply_is_drafted_only_while_the_owner_has_none() -> None:
    seeded = Seeded(uuid.uuid4(), uuid.uuid4(), ())
    first, again = Drafted(existing=0), Drafted(existing=1)
    assert draft_a_reply(OneStory(), first, seeded) == DraftResult(Outcome.DRAFTED)  # type: ignore[arg-type]  # fakes
    assert draft_a_reply(OneStory(), again, seeded) is None and again.asked == 0  # type: ignore[arg-type]  # fakes
