"""Enumerations shared across layers (mirrored by Postgres enums where stored)."""

from enum import StrEnum


class AppStore(StrEnum):
    """Stores whose app reviews SerpSense collects (`app_store` Postgres enum)."""

    GOOGLE_PLAY = "google_play"


class ScanTrigger(StrEnum):
    """Why a scan exists (`scan_trigger` Postgres enum)."""

    SCHEDULE = "schedule"
    MANUAL = "manual"
    REPLAY = "replay"


class ScanStatus(StrEnum):
    """Scan lifecycle state (`scan_status` Postgres enum); transitions live in domain.scan_state."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class TransitionActor(StrEnum):
    """Who changed a scan's status (`transition_actor` Postgres enum)."""

    SYSTEM = "system"
    USER = "user"


class Surface(StrEnum):
    """A search surface a scan collects (`surface` Postgres enum)."""

    SEARCH_PAGE = "search_page"
    AI_OVERVIEW = "ai_overview"
    AUTOCOMPLETE = "autocomplete"
    NEWS = "news"
    TRENDS = "trends"
    PLAY = "play"
    MAPS = "maps"
    YOUTUBE = "youtube"


class SurfaceOutcome(StrEnum):
    """How collecting one surface went (`surface_outcome` Postgres enum)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DISABLED = "disabled"
    NOT_SHOWN = "not_shown"
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_EXHAUSTED = "budget_exhausted"


class SerpEngine(StrEnum):
    """SerpApi engines SerpSense calls (`serp_engine` Postgres enum); values are engine ids."""

    GOOGLE = "google"
    GOOGLE_AI_OVERVIEW = "google_ai_overview"
    GOOGLE_AUTOCOMPLETE = "google_autocomplete"
    GOOGLE_NEWS = "google_news"
    GOOGLE_TRENDS = "google_trends"
    GOOGLE_PLAY_PRODUCT = "google_play_product"
    GOOGLE_MAPS = "google_maps"
    GOOGLE_MAPS_REVIEWS = "google_maps_reviews"
    YOUTUBE = "youtube"


class ServedFrom(StrEnum):
    """Where a SerpApi result came from (`served_from`); only `live` is billable."""

    LOCAL_CACHE = "local_cache"
    SERPAPI_CACHE = "serpapi_cache"
    LIVE = "live"


class SerpCallOutcome(StrEnum):
    """How a SerpApi call went (`serp_call_outcome`); skipped calls never reached SerpApi."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_BUDGET = "skipped_budget"
    CIRCUIT_OPEN = "circuit_open"
