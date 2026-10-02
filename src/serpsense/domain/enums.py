"""Enumerations shared across layers (mirrored by Postgres enums where stored)."""

from enum import StrEnum


class AppStore(StrEnum):
    """Stores whose app reviews SerpSense collects (`app_store` Postgres enum)."""

    GOOGLE_PLAY = "google_play"
