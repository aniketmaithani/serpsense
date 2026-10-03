"""The collector runner with a scripted search service and stub collectors."""

import threading
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from serpsense.domain.collection import Outcome
from serpsense.domain.enums import SerpCallOutcome, SerpErrorCode, Surface, SurfaceOutcome
from serpsense.domain.settings.search import resolve
from serpsense.ports.clock import Clock
from serpsense.ports.collector import Lead, Reading, Subject, Target
from serpsense.ports.search_provider import SearchFailed, SearchRequest
from serpsense.services import collection
from serpsense.services.collection import DEADLINE_PASSED, Collected, CollectorRunner
from serpsense.services.search import SearchResult, SearchSkipped, SkipReason
from tests.fakes import FixedClock, ScriptedSearch, StubCollector, TickingClock, Untouchable

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
OWNER, SCAN = uuid.uuid4(), uuid.uuid4()
FAILED = SearchFailed(SerpErrorCode.HTTP_5XX, http_status=503, latency_ms=40)
CIRCUIT = SearchSkipped(SerpCallOutcome.CIRCUIT_OPEN, SkipReason.CIRCUIT_OPEN)
NO_BUDGET = SearchSkipped(SerpCallOutcome.SKIPPED_BUDGET, SkipReason.MONTHLY_BUDGET)
OVERVIEW = frozenset({Surface.AI_OVERVIEW})
DISABLED, SUCCEEDED = SurfaceOutcome.DISABLED, SurfaceOutcome.SUCCEEDED


def collect(
    searcher: ScriptedSearch,
    *collectors: StubCollector,
    most: int = 25,
    deadline: datetime = NOW + timedelta(1),
    clock: Clock | None = None,
    workers: int = 2,
) -> Collected:
    target = Target(Subject(uuid.uuid4(), "Ola"), resolve({"max_searches": most}))
    runner = CollectorRunner(collectors, searcher, clock or FixedClock(NOW), workers=workers)
    return runner.collect(target, user_id=OWNER, scan_id=SCAN, deadline=deadline)


def pages(script: Mapping[str, object], *queries: str, pages: int = 1) -> Collected:
    """Search pages for `queries` whose first page may also show an AI Overview."""
    lead = StubCollector(Surface.SEARCH_PAGE, *queries, pages=pages, also=OVERVIEW)
    return collect(ScriptedSearch(script), lead)


def asked(collected: Collected) -> list[str]:
    return [str(lead.request.params["q"]) for lead, _ in collected.readings]


def outcome(collected: Collected, surface: Surface) -> SurfaceOutcome:
    return collected.outcomes[surface].outcome


def test_every_lead_is_asked_and_every_surface_gets_an_outcome() -> None:
    searcher = ScriptedSearch({"complaint": {"shows": []}})
    news = StubCollector(Surface.NEWS, "news", "complaint")
    collected = collect(searcher, news, StubCollector(Surface.AUTOCOMPLETE, "ola "))
    assert asked(collected) == ["news", "complaint", "ola "]  # in plan order, every one
    assert sorted(searcher.asked) == sorted(asked(collected))
    assert set(searcher.billed) == {(OWNER, SCAN)}  # billed to the brand's owner, in the scan
    on = {s: o.outcome for s, o in collected.outcomes.items() if o.outcome is not DISABLED}
    assert on == {Surface.NEWS: SUCCEEDED, Surface.AUTOCOMPLETE: SUCCEEDED}
    assert set(collected.outcomes) == set(Surface)  # the rest no collector enables: disabled


def test_an_answer_leads_to_every_follow_up_it_names() -> None:
    collected = pages({"1": {"next": "2", "overview": "ai"}, "2": {"next": "3"}}, "1", pages=3)
    assert asked(collected) == ["1", "2", "ai", "3"]
    assert outcome(collected, Surface.SEARCH_PAGE) is SurfaceOutcome.SUCCEEDED
    assert outcome(collected, Surface.AI_OVERVIEW) is SurfaceOutcome.SUCCEEDED


def test_the_scans_searches_go_to_the_first_leads_and_the_rest_are_cut() -> None:
    searcher = ScriptedSearch({"reviews 1": {"next": "reviews 2"}})
    collected = collect(
        searcher,
        StubCollector(Surface.SEARCH_PAGE, "reviews 1", pages=2),
        StubCollector(Surface.NEWS, "ola news", "ola complaint"),
        most=3,
    )
    assert sorted(searcher.asked) == ["ola news", "reviews 1", "reviews 2"]
    assert outcome(collected, Surface.SEARCH_PAGE) is SurfaceOutcome.SUCCEEDED
    assert outcome(collected, Surface.NEWS) is SurfaceOutcome.BUDGET_EXHAUSTED


def test_a_lead_reserves_its_pages_and_a_search_for_each_other_surface_it_may_show() -> None:
    script = {"1": {"next": "2", "overview": "ai"}, "2": {"next": "3"}}
    lead = StubCollector(Surface.SEARCH_PAGE, "1", pages=3, also=OVERVIEW)
    assert asked(collect(ScriptedSearch(script), lead, most=4)) == ["1", "2", "ai", "3"]
    cut = collect(ScriptedSearch(script), lead, most=3)  # one short of what the estimator counts
    assert asked(cut) == ["1", "2", "ai"]  # the last asked for is cut
    assert outcome(cut, Surface.SEARCH_PAGE) is SurfaceOutcome.BUDGET_EXHAUSTED
    assert outcome(cut, Surface.AI_OVERVIEW) is SurfaceOutcome.SUCCEEDED
    both = collect(ScriptedSearch(script), lead, most=2)  # the cut lead and those queued behind
    assert asked(both) == ["1", "2"]
    assert outcome(both, Surface.SEARCH_PAGE) is SurfaceOutcome.BUDGET_EXHAUSTED
    assert outcome(both, Surface.AI_OVERVIEW) is SurfaceOutcome.BUDGET_EXHAUSTED


def test_a_leads_reservation_counts_the_other_surfaces_it_may_show() -> None:
    script = {"ola": {"overview": "ai"}}
    first = StubCollector(Surface.SEARCH_PAGE, "ola", also=OVERVIEW)
    collected = collect(ScriptedSearch(script), first, StubCollector(Surface.NEWS, "news"), most=2)
    assert asked(collected) == ["ola", "ai"]  # the page and its overview come before the news
    assert outcome(collected, Surface.NEWS) is SurfaceOutcome.BUDGET_EXHAUSTED


def test_a_spent_budget_ends_the_leads_chain() -> None:
    spent = pages({"1": {"next": "2", "overview": "ai"}, "2": NO_BUDGET}, "1", pages=2)
    assert asked(spent) == ["1"]  # the overview queued behind page 2 is never asked for
    assert outcome(spent, Surface.AI_OVERVIEW) is SurfaceOutcome.BUDGET_EXHAUSTED


def test_a_follow_up_nothing_counted_uses_what_no_lead_reserved() -> None:
    script = {"a": {"overview": "overview a"}, "b": {"overview": "overview b"}}

    def run(most: int) -> Collected:
        leads = StubCollector(Surface.NEWS, "a", "b", enables=OVERVIEW)
        return collect(ScriptedSearch(script), leads, most=most)

    assert sorted(asked(run(4))) == ["a", "b", "overview a", "overview b"]
    one_left = run(3)  # both leads want the one search left over; exactly one gets it
    assert len([query for query in asked(one_left) if query.startswith("overview")]) == 1
    assert outcome(one_left, Surface.AI_OVERVIEW) is SurfaceOutcome.BUDGET_EXHAUSTED


def test_a_failed_search_stops_its_lead_and_the_worst_stop_is_reported() -> None:
    searcher = ScriptedSearch({"down": FAILED, "open": CIRCUIT, "spent": NO_BUDGET})
    collected = collect(
        searcher,
        StubCollector(Surface.NEWS, "fine", "open", "down"),
        StubCollector(Surface.AUTOCOMPLETE, "fine too", "open"),
        StubCollector(Surface.SEARCH_PAGE, "spent"),
    )
    assert collected.outcomes[Surface.NEWS] == Outcome(SurfaceOutcome.FAILED, "serpapi.http_5xx")
    assert asked(collected) == ["fine", "fine too"]  # the answers that came back are kept
    assert outcome(collected, Surface.AUTOCOMPLETE) is SurfaceOutcome.CIRCUIT_OPEN
    assert outcome(collected, Surface.SEARCH_PAGE) is SurfaceOutcome.BUDGET_EXHAUSTED


def test_a_failed_search_loses_only_its_own_lead() -> None:
    failed = pages({"1": {"next": "2", "overview": "ai"}, "2": FAILED}, "1", pages=3)
    assert asked(failed) == ["1", "ai"]  # the overview is still asked for
    assert outcome(failed, Surface.SEARCH_PAGE) is SurfaceOutcome.FAILED
    assert outcome(failed, Surface.AI_OVERVIEW) is SurfaceOutcome.SUCCEEDED
    script = {"1": {"next": "2", "overview": "ai"}, "2": {"next": "3"}, "ai": CIRCUIT}
    closed = pages(script, "1", pages=3)  # another engine's circuit is open
    assert asked(closed) == ["1", "2", "3"]
    assert outcome(closed, Surface.SEARCH_PAGE) is SurfaceOutcome.SUCCEEDED
    assert outcome(closed, Surface.AI_OVERVIEW) is SurfaceOutcome.CIRCUIT_OPEN


def test_no_search_starts_after_the_deadline() -> None:
    searcher = ScriptedSearch()
    collected = collect(searcher, StubCollector(Surface.NEWS, "ola news"), deadline=NOW)
    assert collected.outcomes[Surface.NEWS] == Outcome(SurfaceOutcome.FAILED, DEADLINE_PASSED)
    assert searcher.asked == []
    late = TickingClock(NOW, NOW + timedelta(minutes=2))  # the deadline passes after the first page
    searcher = ScriptedSearch({"p1": {"next": "p2"}})
    pages = StubCollector(Surface.SEARCH_PAGE, "p1", pages=2)
    collected = collect(searcher, pages, deadline=NOW + timedelta(minutes=1), clock=late)
    assert searcher.asked == ["p1"]
    assert collected.outcomes[Surface.SEARCH_PAGE] == Outcome(
        SurfaceOutcome.FAILED, DEADLINE_PASSED
    )


def test_among_equal_stops_the_first_planned_lead_is_reported() -> None:
    timed_out = SearchFailed(SerpErrorCode.TIMEOUT, http_status=None, latency_ms=30_000)
    searcher = ScriptedSearch({"a": timed_out, "b": FAILED})
    collected = collect(searcher, StubCollector(Surface.NEWS, "a", "b"), workers=1)
    assert collected.outcomes[Surface.NEWS] == Outcome(SurfaceOutcome.FAILED, "serpapi.timeout")


def test_a_surface_a_lead_may_show_shares_that_leads_fate() -> None:
    # One template's page shows no overview; another's fails, so the overview went unseen.
    unseen = pages(
        {"ola": {"not_shown": [Surface.AI_OVERVIEW]}, "reviews": FAILED}, "ola", "reviews"
    )
    assert outcome(unseen, Surface.AI_OVERVIEW) is SurfaceOutcome.FAILED
    # A later page can't show one, so its failure leaves the overview "not shown".
    later = pages(
        {"1": {"next": "2", "not_shown": [Surface.AI_OVERVIEW]}, "2": FAILED}, "1", pages=2
    )
    assert outcome(later, Surface.AI_OVERVIEW) is SurfaceOutcome.NOT_SHOWN
    assert outcome(later, Surface.SEARCH_PAGE) is SurfaceOutcome.FAILED


def test_a_surface_is_shown_by_its_mentions_or_its_own_answered_lead() -> None:
    inline = pages({"ola": {"shows": [Surface.SEARCH_PAGE, Surface.AI_OVERVIEW]}}, "ola")
    assert outcome(inline, Surface.AI_OVERVIEW) is SurfaceOutcome.SUCCEEDED
    gone = {"shows": [], "not_shown": [Surface.AI_OVERVIEW]}
    expired = pages({"ola": {"overview": "token"}, "token": gone}, "ola")
    assert outcome(expired, Surface.AI_OVERVIEW) is SurfaceOutcome.NOT_SHOWN
    nothing = pages({"ola": {"shows": []}}, "ola")
    assert outcome(nothing, Surface.AI_OVERVIEW) is SurfaceOutcome.NOT_SHOWN
    assert outcome(nothing, Surface.SEARCH_PAGE) is SurfaceOutcome.SUCCEEDED  # its lead answered


class Ignoring(StubCollector):
    def read(self, lead: Lead, payload: Mapping[str, Any]) -> Reading:
        assert isinstance(payload, Untouchable)
        return Reading()


def test_the_runner_hands_payloads_on_unread() -> None:
    collected = collect(ScriptedSearch({"ola": Untouchable()}), Ignoring(Surface.NEWS, "ola"))
    assert asked(collected) == ["ola"]


def test_an_unexpected_error_stops_running_leads_before_their_next_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    known = threading.Event()

    class Watched(threading.Event):
        def set(self) -> None:
            super().set()
            known.set()  # the runner has heard of the error

    class Slow(ScriptedSearch):
        def search(
            self, request: SearchRequest, *, user_id: uuid.UUID, scan_id: uuid.UUID | None = None
        ) -> SearchResult:
            if request.params["q"] == "slow":
                assert known.wait(timeout=5)  # answered only once the error is known
            return super().search(request, user_id=user_id, scan_id=scan_id)

    monkeypatch.setattr(collection, "Event", Watched)
    searcher = Slow({"slow": {"next": "slow 2"}, "boom": RuntimeError("ledger down")})
    with pytest.raises(RuntimeError, match="ledger down"):
        collect(searcher, StubCollector(Surface.NEWS, "slow", "boom", pages=2))
    assert "slow 2" not in searcher.asked


def test_an_unexpected_error_stops_every_lead_before_its_next_search_and_propagates() -> None:
    searcher = ScriptedSearch({"first": RuntimeError("ledger down")})
    with pytest.raises(RuntimeError, match="ledger down"):
        collect(searcher, StubCollector(Surface.NEWS, "first", "second", "third"), workers=1)
    assert searcher.asked == ["first"]


def test_a_surface_that_stopped_short_is_logged_with_its_code() -> None:
    with capture_logs() as logs:
        collect(ScriptedSearch({"ola": FAILED}), StubCollector(Surface.NEWS, "ola"))
    stopped = [entry for entry in logs if entry["event"] == "surface.stopped"]
    assert stopped == [
        {"event": "surface.stopped", "log_level": "info", "surface": Surface.NEWS}
        | {"outcome": SurfaceOutcome.FAILED, "code": "serpapi.http_5xx"}
    ]


def test_the_callers_log_context_reaches_every_search() -> None:
    searcher = ScriptedSearch()
    with structlog.contextvars.bound_contextvars(scan_id=str(SCAN)):
        collect(searcher, StubCollector(Surface.NEWS, "a", "b", "c"))
    assert [context.get("scan_id") for context in searcher.context] == [str(SCAN)] * 3


def test_each_surface_has_one_collector_and_leads_serve_only_what_it_enables() -> None:
    with pytest.raises(ValueError, match="two collectors"):
        collect(ScriptedSearch(), StubCollector(Surface.NEWS), StubCollector(Surface.NEWS))
    overview = ScriptedSearch({"ola": {"overview": "token"}})  # AI Overview isn't enabled
    with pytest.raises(ValueError, match="doesn't enable"):
        collect(overview, StubCollector(Surface.NEWS, "ola"))
    assert overview.asked == ["ola"]
    unenabled = StubCollector(Surface.SEARCH_PAGE, "ola", also=OVERVIEW)
    unenabled.enabled = lambda target: frozenset({Surface.SEARCH_PAGE})  # type: ignore[method-assign]
    planned = ScriptedSearch()
    with pytest.raises(ValueError, match="doesn't enable"):
        collect(planned, unenabled)  # refused when planned, before any search
    assert planned.asked == []
    with pytest.raises(ValueError, match="timezone"):
        collect(ScriptedSearch(), StubCollector(Surface.NEWS), deadline=NOW.replace(tzinfo=None))
