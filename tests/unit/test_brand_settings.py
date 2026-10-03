"""The settings page's reads: the knobs, the estimate under every limit, and previews."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from serpsense.domain.estimator import BrandFacts
from serpsense.domain.schedule import InvalidSchedule
from serpsense.ports.scheduled_brands import ScanInputs
from serpsense.ports.unit_of_work import UnitOfWork
from serpsense.services.brand_settings import BrandSettings, Knobs
from tests.fakes import FakeUnitOfWork, FixedClock, InMemoryScans, StaticSchedules

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
OWNER, OLA = uuid.uuid4(), uuid.uuid4()
OLA_SETTINGS: dict[str, Any] = {
    "max_searches": 8,
    "autocomplete": {"prefixes": ["{brand} "]},
    "news": {"enabled": True},
    "maps": {"enabled": False},
    "play": {"review_pages": 1},
}
INPUTS = ScanInputs(
    user_defaults={"max_searches": 30},
    brand_settings=OLA_SETTINGS,
    languages=("en",),
    facts=BrandFacts(apps=1, locations=0),
    name="Ola",
    interval_minutes=720,
)


def service(inputs: ScanInputs = INPUTS, admin: int = 20) -> BrandSettings:
    uow = FakeUnitOfWork(InMemoryScans(), StaticSchedules(owned={(OWNER, OLA): inputs}))
    factory: UnitOfWork = uow
    return BrandSettings(lambda: factory, FixedClock(NOW), max_searches_per_scan=admin)


def test_the_page_shows_the_brands_knobs_and_what_they_cost() -> None:
    view = service().view(OWNER, OLA)
    assert view is not None and view.name == "Ola" and view.languages == ("en",)
    k = view.knobs
    assert (k.max_searches, k.interval_minutes, k.maps, k.news) == (8, 720, False, True)
    assert view.per_scan == 8 and view.per_month == 8 * 60  # twice a day for 30 days


def test_a_preview_resolves_the_form_without_saving() -> None:
    settings = service(admin=5)
    view = settings.view(OWNER, OLA)
    assert view is not None
    quieter = Knobs(**{**vars(view.knobs), "trends": False, "interval_minutes": 1440})
    preview = settings.view(OWNER, OLA, quieter)
    assert preview is not None and preview.knobs == quieter
    assert (preview.per_scan, preview.per_month) == (5, 150)  # the admin's 5 caps it
    assert settings.view(OWNER, OLA) == view  # nothing saved


def test_knobs_a_scan_cant_use_raise_and_a_strangers_brand_is_missing() -> None:
    settings = service()
    view = settings.view(OWNER, OLA)
    assert view is not None
    with pytest.raises(ValidationError):
        settings.view(OWNER, OLA, Knobs(**{**vars(view.knobs), "max_searches": 0}))
    with pytest.raises(InvalidSchedule):
        settings.view(OWNER, OLA, Knobs(**{**vars(view.knobs), "interval_minutes": 5}))
    assert settings.view(uuid.uuid4(), OLA) is None


def test_manual_brands_cost_nothing_a_month_and_broken_settings_show_the_defaults() -> None:
    view = service(replace(INPUTS, interval_minutes=None)).view(OWNER, OLA)
    assert view is not None and view.knobs.interval_minutes is None and view.per_month == 0
    broken = service(replace(INPUTS, brand_settings={"max_searches": "lots"})).view(OWNER, OLA)
    assert broken is not None and broken.broken and broken.knobs.max_searches == 30
