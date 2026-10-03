"""A new scan is checked before it reaches the database (data-model §4)."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from serpsense.domain.enums import ScanTrigger
from serpsense.ports.scan_store import NewScan

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def scheduled(**overrides: Any) -> NewScan:
    values: dict[str, Any] = {
        "brand_id": uuid.uuid4(),
        "trigger": ScanTrigger.SCHEDULE,
        "scheduled_for": NOW,
        "settings_snapshot": {"preset": "lean"},
        "estimated_searches": 6,
        "created_at": NOW,
    }
    return NewScan(**{**values, **overrides})


@pytest.mark.parametrize(
    "overrides",
    [
        {"scheduled_for": None},  # a scheduled scan needs its slot
        {"trigger": ScanTrigger.MANUAL},  # a manual scan has no slot and needs a requester
        {"estimated_searches": -1},
    ],
)
def test_a_scan_the_database_would_reject_is_refused_first(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        scheduled(**overrides)


def test_scheduled_and_manual_scans_are_accepted() -> None:
    assert scheduled().scheduled_for == NOW
    user = uuid.uuid4()
    manual = scheduled(trigger=ScanTrigger.MANUAL, scheduled_for=None, requested_by=user)
    assert manual.requested_by == user
