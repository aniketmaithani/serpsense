"""Port for a scan's scores (data-model §7).

The results are written once per scan, in the unit of work that finishes it. A store works
inside the caller's unit of work and never commits.
"""

import uuid
from datetime import datetime
from typing import Protocol

from serpsense.domain.scoring.scan import ScanScores


class ScoreStore(Protocol):
    def record(self, scan_id: uuid.UUID, scores: ScanScores, *, version: str, at: datetime) -> bool:
        """The scan's scores under a scoring version; False when it was already scored."""
        ...
