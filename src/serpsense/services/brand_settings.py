"""A brand's search settings and schedule, as its page shows and saves them (BUILD_PLAN §6, §13).

Saves write a version only when something changed (data-model §3), and the brand's layer keeps
only knobs that differ from the owner's defaults (or that it already set). The schedule is an
interval or manual only, keeping its timezone and quiet hours. The estimate is the most searches
a scan makes under every limit, and per 30-day month. Another user's or an archived brand reads
as missing; stored settings that no longer resolve are shown as the defaults, flagged.
"""

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from serpsense.domain.estimator import estimate
from serpsense.domain.schedule import InvalidSchedule, Schedule
from serpsense.domain.settings.search import PRESETS, Preset, SearchSettings, for_brand, merge
from serpsense.ports.clock import Clock
from serpsense.ports.scheduled_brands import ScanInputs
from serpsense.ports.unit_of_work import UnitOfWorkFactory

MONTH_MINUTES = 30 * 24 * 60


class Saved(StrEnum):
    SAVED = "saved"
    UNCHANGED = "unchanged"  # the same values as the latest versions
    MISSING = "missing"  # not the asker's brand, archived, or no brand at all
    INVALID = "invalid"  # values a scan can't use


@dataclass(frozen=True, kw_only=True)
class Knobs:
    """What the settings form edits; everything else in the brand's layer is kept."""

    max_searches: int
    search_page: bool
    ai_overview: bool
    autocomplete: bool
    news: bool
    trends: bool
    play: bool
    play_review_pages: int
    maps: bool
    interval_minutes: int | None  # None: manual scans only

    @classmethod
    def of(cls, settings: SearchSettings, interval_minutes: int | None) -> "Knobs":
        return cls(
            max_searches=settings.max_searches,
            search_page=settings.search_page.enabled,
            ai_overview=settings.search_page.ai_overview,
            autocomplete=settings.autocomplete.enabled,
            news=settings.news.enabled,
            trends=settings.trends.enabled,
            play=settings.play.enabled,
            play_review_pages=settings.play.review_pages,
            maps=settings.maps.enabled,
            interval_minutes=interval_minutes,
        )

    def layer(self) -> dict[str, Any]:
        """The part of a brand's settings document these knobs set."""
        return {
            "max_searches": self.max_searches,
            "search_page": {"enabled": self.search_page, "ai_overview": self.ai_overview},
            "autocomplete": {"enabled": self.autocomplete},
            "news": {"enabled": self.news},
            "trends": {"enabled": self.trends},
            "play": {"enabled": self.play, "review_pages": self.play_review_pages},
            "maps": {"enabled": self.maps},
        }


@dataclass(frozen=True, kw_only=True)
class SettingsView:
    brand_id: uuid.UUID
    name: str
    knobs: Knobs
    languages: tuple[str, ...]
    per_scan: int  # searches a scan makes at most, under every limit
    per_month: int  # 0 when manual only
    admin_limit: int
    broken: bool = False  # the stored settings no longer resolve; the defaults are shown


# A save's new brand layer and interval, from what the brand has now.
Change = tuple[Mapping[str, Any], int | None]


class BrandSettings:
    def __init__(
        self, unit_of_work: UnitOfWorkFactory, clock: Clock, *, max_searches_per_scan: int
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._admin_limit = max_searches_per_scan

    def view(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, knobs: Knobs | None = None
    ) -> SettingsView | None:
        """The brand's settings, or a preview of them with `knobs`; None when the brand is
        missing. Raises ValidationError or InvalidSchedule for knobs a scan can't use."""
        with self._unit_of_work() as uow:
            inputs = uow.schedules.scan_inputs(user_id, brand_id, as_of=self._clock.now())
        if inputs is None:
            return None
        if knobs is not None:
            document = _layered(inputs, knobs)
            settings = _resolved(inputs, document)
            return self._view(brand_id, inputs, settings, knobs.interval_minutes)
        try:
            settings, broken = _resolved(inputs, inputs.brand_settings), False
        except ValidationError:
            settings, broken = _resolved(inputs, {}), True
        return self._view(brand_id, inputs, settings, inputs.interval_minutes, broken=broken)

    def save(self, user_id: uuid.UUID, brand_id: uuid.UUID, knobs: Knobs) -> Saved:
        """A new settings and schedule version from the form, each only if it changed."""

        def change(inputs: ScanInputs) -> Change:
            return _layered(inputs, knobs), knobs.interval_minutes

        return self._write(user_id, brand_id, change)

    def apply_preset(self, user_id: uuid.UUID, brand_id: uuid.UUID, preset: Preset) -> Saved:
        """The brand's layer replaced by a preset's (BUILD_PLAN §6.3); the schedule is kept."""
        return self._write(user_id, brand_id, lambda i: (PRESETS[preset], i.interval_minutes))

    def _view(
        self,
        brand_id: uuid.UUID,
        inputs: ScanInputs,
        settings: SearchSettings,
        interval: int | None,
        *,
        broken: bool = False,
    ) -> SettingsView:
        if interval is not None:
            _schedule(inputs, interval)  # a preview of an interval that isn't one raises
        capped = settings.capped(self._admin_limit)
        per_scan = min(estimate(capped, inputs.facts), capped.max_searches)
        scans = 0 if interval is None else MONTH_MINUTES // interval
        return SettingsView(
            brand_id=brand_id,
            name=inputs.name,
            knobs=Knobs.of(settings, interval),
            languages=capped.languages,
            per_scan=per_scan,
            per_month=per_scan * scans,
            admin_limit=self._admin_limit,
            broken=broken,
        )

    def _write(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, change: Callable[[ScanInputs], Change]
    ) -> Saved:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            inputs = uow.schedules.scan_inputs(user_id, brand_id, as_of=now)
            if inputs is None:
                return Saved.MISSING
            document, interval = change(inputs)
            try:
                _resolved(inputs, document)
                schedule = None if interval is None else _schedule(inputs, interval)
            except (ValidationError, InvalidSchedule):
                return Saved.INVALID
            changed = False
            if _plain(document) != _plain(inputs.brand_settings):
                changed = uow.brands.set_search_settings(brand_id, document, at=now)
            if schedule is not None:
                changed = uow.brands.set_schedule(brand_id, schedule, at=now) or changed
            elif inputs.interval_minutes is not None:
                changed = uow.brands.stop_schedule(brand_id, at=now) or changed
        return Saved.SAVED if changed else Saved.UNCHANGED


def _resolved(inputs: ScanInputs, document: Mapping[str, Any]) -> SearchSettings:
    return for_brand(inputs.user_defaults, document, inputs.languages)


def _layered(inputs: ScanInputs, knobs: Knobs) -> dict[str, Any]:
    """The brand's layer with the form's knobs, keeping only those the layer already set or that
    differ from the owner's defaults beneath it."""
    below = _resolved(inputs, {}).model_dump(mode="json")
    current = inputs.brand_settings
    kept: dict[str, Any] = {}
    for key, value in knobs.layer().items():
        if isinstance(value, Mapping):
            stored = current.get(key)
            mine: Mapping[str, Any] = stored if isinstance(stored, Mapping) else {}
            part = {k: v for k, v in value.items() if k in mine or below[key][k] != v}
            if part:
                kept[key] = part
        elif key in current or below[key] != value:
            kept[key] = value
    return merge(current, kept)


def _schedule(inputs: ScanInputs, interval: int) -> Schedule:
    return Schedule(interval, inputs.timezone, inputs.quiet_start, inputs.quiet_end)


def _plain(document: Mapping[str, Any]) -> str:
    """A document as JSONB would hold it, to compare (tuples are lists)."""
    return json.dumps(document, sort_keys=True)
