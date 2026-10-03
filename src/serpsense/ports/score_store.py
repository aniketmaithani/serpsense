"""Port for a scan's scores: what scoring reads, and the results (data-model §7).

The inputs are derived from what the brand's scans observed and how their mentions were labelled
under the active prompt of each labelling task; the results are written once per scan, in the
unit of work that finishes it. A store works inside the caller's unit of work and never commits.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from serpsense.domain.enums import LlmTask
from serpsense.domain.scoring.scan import ScanScores, ScoreInputs


class ScoreStore(Protocol):
    def inputs(self, scan_id: uuid.UUID, *, prompts: Mapping[LlmTask, str]) -> ScoreInputs:
        """What scoring the scan reads: its labelled mentions about the brand (each by its own
        task, the active prompt's label first), app ratings and newest reviews, and the brand's
        eight newest earlier scored scans."""
        ...

    def unscored(self) -> list[uuid.UUID]:
        """Scans that succeeded or are partial but have no scores, oldest first."""
        ...

    def record(self, scan_id: uuid.UUID, scores: ScanScores, *, version: str, at: datetime) -> bool:
        """The scan's scores under a scoring version; False when it was already scored."""
        ...
