"""Score a scan as it finishes (BUILD_PLAN §11, docs/scoring.md).

Scoring reads what the brand's scans recorded and applies the pure rules in `domain/scoring/`.
It runs in the unit of work that finishes the scan, so the scores commit with its ending, and
only for a scan that succeeded or is partial. A scan is scored once.
"""

import uuid
from datetime import datetime

from serpsense.domain import scoring
from serpsense.domain.enums import ScanStatus
from serpsense.domain.labelling import PROMPTS
from serpsense.domain.scoring.scan import ScanScores, score
from serpsense.ports.unit_of_work import UnitOfWork

SCORED = frozenset({ScanStatus.SUCCEEDED, ScanStatus.PARTIAL})


def scores_for(uow: UnitOfWork, scan_id: uuid.UUID) -> ScanScores:
    """The scan's scores under the current scoring version, from its labelled mentions."""
    return score(uow.scores.inputs(scan_id, prompts=PROMPTS))


def record(uow: UnitOfWork, scan_id: uuid.UUID, scores: ScanScores, *, at: datetime) -> bool:
    """Whether the scores were written now; False when the scan was scored already."""
    return uow.scores.record(scan_id, scores, version=scoring.VERSION, at=at)
