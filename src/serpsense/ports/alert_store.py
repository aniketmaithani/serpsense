"""Port for alerts and in-app notifications (data-model §8, BUILD_PLAN §12).

The store reads what the alert rules need about a finished scan, and writes an alert once per
(scan, rule) and its notification once, inside the unit of work that finishes the scan. It never
commits.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from serpsense.domain.alert_rules import AlertFacts
from serpsense.domain.enums import AlertRule


@dataclass(frozen=True)
class ScanAlertContext:
    """What the rules read about a scored scan, and what its notification says."""

    facts: AlertFacts
    brand_name: str
    competitor: bool  # the brand is tracked as a competitor of another brand
    health: int | None
    crisis: int


class AlertStore(Protocol):
    def context(self, scan_id: uuid.UUID) -> ScanAlertContext | None:
        """None for a scan that wasn't scored."""
        ...

    def fire(self, scan_id: uuid.UUID, rule: AlertRule, *, at: datetime) -> uuid.UUID | None:
        """The new alert's id; None when the rule already fired for the scan."""
        ...

    def notify(self, alert_id: uuid.UUID, *, title: str, body: str, at: datetime) -> bool:
        """Tell the owner of the alert's brand; False when they were told already."""
        ...
