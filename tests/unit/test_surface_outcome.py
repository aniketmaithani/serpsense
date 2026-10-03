"""A surface's outcome from how the leads that could show it went."""

import pytest

from serpsense.domain.collection import Outcome, surface_outcome
from serpsense.domain.enums import SurfaceOutcome

pytestmark = pytest.mark.unit

FAILED = Outcome(SurfaceOutcome.FAILED, "serpapi.http_5xx")
TIMED_OUT = Outcome(SurfaceOutcome.FAILED, "scan.deadline_passed")
CIRCUIT = Outcome(SurfaceOutcome.CIRCUIT_OPEN)
SPENT = Outcome(SurfaceOutcome.BUDGET_EXHAUSTED)


@pytest.mark.parametrize(
    ("stops", "expected"),
    [
        ([SPENT, CIRCUIT, FAILED], FAILED),
        ([SPENT, CIRCUIT], CIRCUIT),
        ([SPENT, SPENT], SPENT),
        ([TIMED_OUT, FAILED], TIMED_OUT),  # the first of equals
    ],
)
def test_a_surface_reports_the_worst_way_its_leads_stopped(
    stops: list[Outcome], expected: Outcome
) -> None:
    assert surface_outcome(stops, shown=True) == expected


def test_a_surface_whose_leads_were_all_answered_succeeded_if_shown() -> None:
    assert surface_outcome([], shown=True) == Outcome(SurfaceOutcome.SUCCEEDED)
    assert surface_outcome([], shown=False) == Outcome(SurfaceOutcome.NOT_SHOWN)


@pytest.mark.parametrize("outcome", [SurfaceOutcome.SUCCEEDED, SurfaceOutcome.DISABLED])
def test_a_lead_only_stops_in_one_of_three_ways(outcome: SurfaceOutcome) -> None:
    with pytest.raises(ValueError, match="stops"):
        surface_outcome([FAILED, Outcome(outcome)], shown=True)


def test_the_stops_may_come_from_any_iterable() -> None:
    assert surface_outcome((stop for stop in [SPENT, FAILED]), shown=False) == FAILED


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (SurfaceOutcome.FAILED, None),
        (SurfaceOutcome.SUCCEEDED, "serpapi.timeout"),
        (SurfaceOutcome.FAILED, ""),
        (SurfaceOutcome.FAILED, "Bad Code!"),
        (SurfaceOutcome.FAILED, "x" * 65),
    ],
)
def test_an_outcome_has_a_lowercase_error_code_exactly_when_it_failed(
    outcome: SurfaceOutcome, code: str | None
) -> None:
    with pytest.raises(ValueError, match="error code"):
        Outcome(outcome, code)
