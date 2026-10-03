"""How collecting a surface went (data-model §4, `scan_surface_results`).

A surface's outcome follows from the leads that could have shown it: if any went unanswered it
reports the worst way one stopped; otherwise it succeeded if an answer showed it, and had nothing
to show if none did.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from serpsense.domain.enums import SurfaceOutcome

# Ways a surface can stop short, the worst first: the one a surface reports.
STOPS = (SurfaceOutcome.FAILED, SurfaceOutcome.CIRCUIT_OPEN, SurfaceOutcome.BUDGET_EXHAUSTED)
# The format the table checks (`ck_scan_surface_results_error_code_format`).
ERROR_CODE = re.compile(r"[a-z][a-z0-9_.]{0,63}")


@dataclass(frozen=True)
class Outcome:
    outcome: SurfaceOutcome
    error_code: str | None = None  # exactly for a failed surface, e.g. `serpapi.http_5xx`

    def __post_init__(self) -> None:
        if (self.error_code is None) == (self.outcome is SurfaceOutcome.FAILED):
            raise ValueError("an error code exactly for a failed surface")
        if self.error_code is not None and not ERROR_CODE.fullmatch(self.error_code):
            raise ValueError("an error code is a lowercase dotted name")


def surface_outcome(stops: Iterable[Outcome], *, shown: bool) -> Outcome:
    """`stops` are how the unanswered leads that could have shown the surface stopped; among
    equally bad stops the first given is reported."""
    stops = list(stops)
    if any(stop.outcome not in STOPS for stop in stops):
        raise ValueError("a lead stops by failing, by an open circuit or by a spent budget")
    for worst in STOPS:
        for stop in stops:
            if stop.outcome is worst:
                return stop
    return Outcome(SurfaceOutcome.SUCCEEDED if shown else SurfaceOutcome.NOT_SHOWN)
