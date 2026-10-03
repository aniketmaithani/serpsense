"""Port for what a scan observes besides mentions: Trends lines and app ratings (data-model §5).

Observations are append-only and written once per scan: a retried scan writes nothing again. A
store works inside the caller's unit of work and never commits.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from serpsense.domain.observation import MAX_COMPARISON, AppRating, TrendsPoint


@dataclass(frozen=True)
class Comparison:
    """A Trends joint query as sent: `subjects[i]` is the brand whose name was the i-th query.
    Lines are mapped by place, never by name."""

    scan_id: uuid.UUID
    subjects: tuple[uuid.UUID, ...]
    points: tuple[TrendsPoint, ...]

    def __post_init__(self) -> None:
        if len(set(self.subjects)) != len(self.subjects):
            raise ValueError("a brand appears once in a comparison")
        if not 1 <= len(self.subjects) <= MAX_COMPARISON:
            raise ValueError("a comparison has one to five brands")
        if any(point.query_index >= len(self.subjects) for point in self.points):
            raise ValueError("every point belongs to one of the subjects")
        moments = {(point.query_index, point.observed_at) for point in self.points}
        if len(moments) != len(self.points):
            raise ValueError("a line has one point per time")


class ObservationStore(Protocol):
    def record_trends(self, comparison: Comparison) -> int:
        """Every line of the comparison, all or nothing: a scan that already has its comparison
        keeps it, even if a competitor was unlinked since. Returns how many points were written."""
        ...

    def record_app_rating(
        self, scan_id: uuid.UUID, brand_app_id: uuid.UUID, rating: AppRating
    ) -> bool:
        """The app's rating as the scan saw it; False when it was already recorded."""
        ...
