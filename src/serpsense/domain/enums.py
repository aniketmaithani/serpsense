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
