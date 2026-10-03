"""Score a scan as it finishes (BUILD_PLAN §11, docs/scoring.md).

Scoring reads what the brand's scans recorded and applies the pure rules in `domain/scoring/`.
It runs in the unit of work that finishes the scan, so the scores commit with its ending, and
only for a scan that succeeded or is partial. A scan is scored once. Scans that finished before
scoring existed are scored by `score_backlog`, oldest first, without raising alerts: an alert is
news, and theirs is old.
"""

import uuid
from datetime import datetime

from serpsense.domain import scoring
from serpsense.domain.enums import ScanStatus
from serpsense.domain.labelling import PROMPTS
from serpsense.domain.scoring.scan import ScanScores, score
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

log = get_logger(__name__)

SCORED = frozenset({ScanStatus.SUCCEEDED, ScanStatus.PARTIAL})


def scores_for(uow: UnitOfWork, scan_id: uuid.UUID) -> ScanScores:
    """The scan's scores under the current scoring version, from its labelled mentions."""
    return score(uow.scores.inputs(scan_id, prompts=PROMPTS))


def record(uow: UnitOfWork, scan_id: uuid.UUID, scores: ScanScores, *, at: datetime) -> bool:
    """Whether the scores were written now; False when the scan was scored already."""
    return uow.scores.record(scan_id, scores, version=scoring.VERSION, at=at)


def score_backlog(unit_of_work: UnitOfWorkFactory, clock: Clock) -> int:
    """Score the finished scans that have no scores, each in its own unit of work, oldest first
    so each one's usual is in place; how many were scored."""
    with unit_of_work() as uow:
        backlog = uow.scores.unscored()
    scored = 0
    for scan_id in backlog:
        try:
            with unit_of_work() as uow:
                if record(uow, scan_id, scores_for(uow, scan_id), at=clock.now()):
                    scored += 1
        except Exception as exc:
            log.error("scan.score_failed", scan_id=str(scan_id), error=type(exc).__name__)
            raise
    return scored
