"""Values a scan observes besides mentions: Trends points and app ratings (data-model §5)."""

from dataclasses import dataclass
from datetime import datetime

MAX_COMPARISON = 5  # the scan's brand and at most four competitors


@dataclass(frozen=True)
class TrendsPoint:
    """One point of one line in a Trends comparison. The collector maps `query_index` (the
    query's place in the comparison it sent) to its brand; names can differ only in case."""

    query: str
    query_index: int  # 0-4, in the order the queries were sent
    observed_at: datetime  # the point's start, UTC
    interest: int  # 0-100 within this comparison ("<1" is 0)
    is_partial: bool  # Google's flag on the last, still-incomplete point

    def __post_init__(self) -> None:
        if isinstance(self.query_index, bool) or not 0 <= self.query_index < MAX_COMPARISON:
            raise ValueError("a comparison has at most five lines")
        if not 0 <= self.interest <= 100:
            raise ValueError("Trends interest is 0-100")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True)
class AppRating:
    rating_hundredths: int  # 410 means 4.10 stars
    review_count: int

    def __post_init__(self) -> None:
        if not 100 <= self.rating_hundredths <= 500:
            raise ValueError("a store rating is 1.00-5.00 stars")
        if self.review_count < 0:
            raise ValueError("review count can't be negative")
