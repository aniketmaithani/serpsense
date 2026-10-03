"""A scan's scores in Postgres (data-model §7)."""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table
from sqlalchemy.dialects.postgresql import insert

from serpsense.adapters.db.models.scores import CrisisComponentValue, ScoreRun, SurfaceScore
from serpsense.domain.scoring.scan import ScanScores

RUNS, SURFACE_SCORES = cast(Table, ScoreRun.__table__), cast(Table, SurfaceScore.__table__)
COMPONENTS = cast(Table, CrisisComponentValue.__table__)


class SqlScoreStore:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record(self, scan_id: uuid.UUID, scores: ScanScores, *, version: str, at: datetime) -> bool:
        run = insert(RUNS).values(scan_id=scan_id, version=version, computed_at=at)
        added = self._conn.execute(run.on_conflict_do_nothing().returning(RUNS.c.scan_id))
        if added.first() is None:
            return False  # scored already: the first scores stay
        surfaces = scores.surfaces.items()
        self._insert(SURFACE_SCORES, [{"surface": s, "score": v} for s, v in surfaces], scan_id)
        components = scores.components.items()
        self._insert(COMPONENTS, [{"component": c, "value": v} for c, v in components], scan_id)
        return True

    def _insert(self, table: Table, rows: list[dict[str, object]], scan_id: uuid.UUID) -> None:
        if rows:  # a scan whose surfaces all showed nothing has no surface scores
            self._conn.execute(insert(table), [{"scan_id": scan_id, **row} for row in rows])
