"""A scan's finish scores it: succeeded and partial scans only, once, in the same unit of work."""

import uuid
from dataclasses import replace
from datetime import datetime

import pytest

from serpsense.domain.enums import MentionSource, SerpErrorCode, Surface
from serpsense.domain.labelling import PROMPTS
from serpsense.domain.scan_state import Transition
from serpsense.domain.scoring.scan import Observed, ScoreInputs
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.ports.search_provider import SearchFailed, SearchRequest
from serpsense.services.search import SearchResult
from tests.fakes import ScriptedSearch, StubCollector
from tests.unit.test_scans import MOST, NOW, R, S, Scan, labels, moves, scan

pytestmark = pytest.mark.unit

DOWN = SearchFailed(SerpErrorCode.HTTP_5XX, http_status=503, latency_ms=40)


def test_a_succeeded_scan_is_scored_from_its_inputs_under_s1() -> None:
    run = scan(ScriptedSearch())
    run.uow.scores.given = ScoreInputs(NOW, (Observed(MentionSource.NEWS, 0, published_at=NOW),))
    assert run.service.run(run.scan_id) is S.SUCCEEDED
    assert run.uow.scores.asked == [(run.scan_id, dict(PROMPTS))]
    scores, version, at = run.uow.scores.recorded[run.scan_id]
    assert (dict(scores.surfaces), version, at) == ({Surface.NEWS: 50}, "s1", NOW)


def test_a_partial_scan_is_scored_too() -> None:
    run = scan(ScriptedSearch({"down": DOWN}), StubCollector(Surface.NEWS, "news", "down"))
    assert run.service.run(run.scan_id) is S.PARTIAL
    assert run.scan_id in run.uow.scores.recorded


@pytest.mark.parametrize(
    "options",
    [{"used": 1500 - MOST + 1}, {"target": {"brand_archived": True}}],
    ids=["skipped for its budget", "skipped for its brand"],
)
def test_a_skipped_scan_isnt_scored(options: dict[str, object]) -> None:
    run = scan(ScriptedSearch(), **options)
    assert run.service.run(run.scan_id) is S.SKIPPED
    assert run.uow.scores.asked == [] and run.uow.scores.recorded == {}


def test_a_brand_archived_before_the_finish_isnt_scored() -> None:
    run: Scan

    def archiving(request: LlmRequest) -> str | LlmCallFailed:
        targets = run.uow.targets.targets
        targets[run.scan_id] = replace(targets[run.scan_id], brand_archived=True)
        return labels(request)

    run = scan(ScriptedSearch(), answer=archiving)
    assert run.service.run(run.scan_id) is S.SKIPPED
    assert run.uow.scores.recorded == {}


def test_a_failed_scan_isnt_scored() -> None:
    run = scan(ScriptedSearch({"down": DOWN}), StubCollector(Surface.NEWS, "down"))
    assert run.service.run(run.scan_id) is S.FAILED
    assert run.uow.scores.recorded == {}


def test_a_scan_the_sweep_ended_meanwhile_isnt_scored() -> None:
    run: Scan
    timed_out = Transition(S.RUNNING, S.FAILED, R.TIMED_OUT)

    class Slow(ScriptedSearch):
        def search(
            self, request: SearchRequest, *, user_id: uuid.UUID, scan_id: uuid.UUID | None = None
        ) -> SearchResult:
            assert run.uow.scans.move(run.scan_id, timed_out, at=NOW)
            return super().search(request, user_id=user_id, scan_id=scan_id)

    run = scan(Slow())
    assert run.service.run(run.scan_id) is None
    assert run.uow.scores.recorded == {}


def test_scoring_that_raises_fails_the_scan() -> None:
    run = scan(ScriptedSearch())
    later = datetime(2026, 10, 4, tzinfo=NOW.tzinfo)
    bad = Observed(MentionSource.NEWS, 2, published_at=later)  # a sentiment out of range
    run.uow.scores.given = ScoreInputs(later, (bad,))
    with pytest.raises(ValueError, match="sentiment"):
        run.service.run(run.scan_id)
    assert moves(run)[-1] == (S.RUNNING, S.FAILED, R.STAGE_FAILED)
    assert run.uow.scores.recorded == {}
