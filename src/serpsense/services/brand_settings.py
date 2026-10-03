"""A brand's search settings, languages and schedule, as its page shows and saves them (BUILD_PLAN
§6, §13).

Saves write a version only when something changed (data-model §3), and the brand's layer keeps
only knobs that differ from the owner's defaults (or that it already set). The brand stores its
own languages only once they differ from those its settings give it. The schedule is an
interval or manual only, keeping its timezone and quiet hours. The estimate is the most searches
a scan makes under every limit, and per 30-day month. Another user's or an archived brand reads
as missing; stored settings that no longer resolve are shown as the defaults, flagged.
"""

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from serpsense.domain.estimator import estimate
from serpsense.domain.schedule import InvalidSchedule, Schedule
from serpsense.domain.settings.search import (
    PRESETS,
    DateRange,
    Preset,
    ReviewSort,
    SearchSettings,
    for_brand,
    merge,
    resolve,
)
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
    """What the settings form edits: every brand-level knob. The languages are the brand's own
    (`brand_languages`, over every layer); the rest go in the brand's settings layer."""

    max_searches: int
    languages: tuple[str, ...]
    serpapi_cache: bool
    country: str
    google_domain: str
    search_page: bool
    search_pages: int
    search_templates: tuple[str, ...]
    ai_overview: bool
    autocomplete: bool
    autocomplete_prefixes: tuple[str, ...]
    news: bool
    news_terms: tuple[str, ...]
    trends: bool
    trends_region: str
    trends_range: DateRange
    related_queries: bool
    play: bool
    play_review_pages: int
    play_review_sort: ReviewSort
    maps: bool
    maps_review_pages: int
    maps_review_sort: ReviewSort
    youtube: bool
    youtube_templates: tuple[str, ...]
    interval_minutes: int | None  # None: manual scans only

    @classmethod
    def of(cls, settings: SearchSettings, interval_minutes: int | None) -> "Knobs":
        page, trends = settings.search_page, settings.trends
        return cls(
            max_searches=settings.max_searches,
            languages=settings.languages,
            serpapi_cache=settings.serpapi_cache,
            country=settings.country,
            google_domain=settings.google_domain,
            search_page=page.enabled,
            search_pages=page.pages,
            search_templates=page.templates,
            ai_overview=page.ai_overview,
            autocomplete=settings.autocomplete.enabled,
            autocomplete_prefixes=settings.autocomplete.prefixes,
            news=settings.news.enabled,
            news_terms=settings.news.extra_terms,
            trends=trends.enabled,
            trends_region=trends.region,
            trends_range=trends.date_range,
            related_queries=trends.related_queries,
            play=settings.play.enabled,
            play_review_pages=settings.play.review_pages,
            play_review_sort=settings.play.review_sort,
            maps=settings.maps.enabled,
            maps_review_pages=settings.maps.review_pages,
            maps_review_sort=settings.maps.review_sort,
            youtube=settings.youtube.enabled,
            youtube_templates=settings.youtube.templates,
            interval_minutes=interval_minutes,
        )

    def layer(self) -> dict[str, Any]:
        """The part of a brand's settings document these knobs set, as JSON holds it (lists, not
        tuples), so it compares equal to stored and default values."""
        return {
            "max_searches": self.max_searches,
            "serpapi_cache": self.serpapi_cache,
            "country": self.country,
            "google_domain": self.google_domain,
            "search_page": {
                "enabled": self.search_page,
                "pages": self.search_pages,
                "templates": list(self.search_templates),
                "ai_overview": self.ai_overview,
            },
            "autocomplete": {
                "enabled": self.autocomplete,
                "prefixes": list(self.autocomplete_prefixes),
            },
            "news": {"enabled": self.news, "extra_terms": list(self.news_terms)},
            "trends": {
                "enabled": self.trends,
                "region": self.trends_region,
                "date_range": self.trends_range,
                "related_queries": self.related_queries,
            },
            "play": self._reviews(self.play, self.play_review_pages, self.play_review_sort),
            "maps": self._reviews(self.maps, self.maps_review_pages, self.maps_review_sort),
            "youtube": {"enabled": self.youtube, "templates": list(self.youtube_templates)},
        }

    @staticmethod
    def _reviews(enabled: bool, pages: int, sort: ReviewSort) -> dict[str, Any]:
        return {"enabled": enabled, "review_pages": pages, "review_sort": sort.value}


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
            settings = _resolved(inputs, document, knobs.languages)
            return self._view(brand_id, inputs, settings, knobs.interval_minutes)
        try:
            settings, broken = _resolved(inputs, inputs.brand_settings), False
        except ValidationError:
            settings, broken = _resolved(inputs, {}), True
        return self._view(brand_id, inputs, settings, inputs.interval_minutes, broken=broken)

    def save(self, user_id: uuid.UUID, brand_id: uuid.UUID, knobs: Knobs) -> Saved:
        """A new settings and schedule version and the brand's own languages from the form,
        each only if it changed."""

        def change(inputs: ScanInputs) -> Change:
            return _layered(inputs, knobs), knobs.interval_minutes

        return self._write(user_id, brand_id, change, knobs.languages)

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
        self,
        user_id: uuid.UUID,
        brand_id: uuid.UUID,
        change: Callable[[ScanInputs], Change],
        languages: tuple[str, ...] | None = None,  # None: the brand's stay as they are
    ) -> Saved:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            inputs = uow.schedules.scan_inputs(user_id, brand_id, as_of=now)
            if inputs is None:
                return Saved.MISSING
            document, interval = change(inputs)
            try:
                _resolved(inputs, document, languages)
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
            own = _own_languages(inputs, document, languages)
            if own is not None:
                changed = uow.brands.set_languages(brand_id, own) or changed
        return Saved.SAVED if changed else Saved.UNCHANGED


def _resolved(
    inputs: ScanInputs, document: Mapping[str, Any], languages: Sequence[str] | None = None
) -> SearchSettings:
    """The brand's settings with this layer, and these languages over every layer (none: the
    brand's own, if it has any). An empty list of languages doesn't resolve."""
    if languages is None:
        return for_brand(inputs.user_defaults, document, inputs.languages)
    return resolve(inputs.user_defaults, document, {"languages": tuple(languages)})


def _own_languages(
    inputs: ScanInputs, document: Mapping[str, Any], languages: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """The languages to store as the brand's own, or None to leave them: a brand without its own
    keeps following its settings while the form shows those."""
    if languages is None or set(languages) == set(inputs.languages):
        return None
    if not inputs.languages and set(languages) == set(_resolved(inputs, document).languages):
        return None
    return languages


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
