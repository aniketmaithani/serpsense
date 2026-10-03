"""Scans and their status history in Postgres (data-model §4)."""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from serpsense.adapters.db.models.scans import Scan, ScanStatusTransition
from serpsense.domain.enums import ScanStatus, ScanTrigger, TransitionActor
from serpsense.domain.scan_state import IllegalTransition, Transition, TransitionReason
from serpsense.ports.scan_store import NewScan

SCANS = cast(Table, Scan.__table__)
TRANSITIONS = cast(Table, ScanStatusTransition.__table__)
CREATION_REASON = {
    ScanTrigger.SCHEDULE: TransitionReason.SCHEDULED,
    ScanTrigger.MANUAL: TransitionReason.REQUESTED,
    ScanTrigger.REPLAY: TransitionReason.REPLAYED,
}


class SqlScanStore:
    """Works on the caller's connection and never commits: the unit of work does, so a status
    change commits or rolls back together with whatever else the caller wrote."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def create(self, scan: NewScan) -> uuid.UUID | None:
        row = {
            "id": uuid.uuid4(),
            "brand_id": scan.brand_id,
            "trigger": scan.trigger,
            "scheduled_for": scan.scheduled_for,
            "requested_by": scan.requested_by,
            "status": ScanStatus.QUEUED,
            "settings_snapshot": dict(scan.settings_snapshot),
            "estimated_searches": scan.estimated_searches,
            "created_at": scan.created_at,
        }
        # No conflict target: both the slot and the one-active-scan rule must apply.
        statement = pg_insert(SCANS).values(row).on_conflict_do_nothing().returning(SCANS.c.id)
        user = scan.requested_by
        created = Transition(
            None,
            ScanStatus.QUEUED,
            CREATION_REASON[scan.trigger],
            TransitionActor.USER if user else TransitionActor.SYSTEM,
            user,
        )
        scan_id: uuid.UUID | None = self._conn.execute(statement).scalar_one_or_none()
        if scan_id is not None:
            _record(self._conn, scan_id, created, scan.created_at)
        return scan_id

    def move(self, scan_id: uuid.UUID, transition: Transition, *, at: datetime) -> bool:
        if transition.from_status is None:
            raise IllegalTransition("a scan is created, never moved into existence")
        statement = (
            update(SCANS)
            .where(SCANS.c.id == scan_id, SCANS.c.status == transition.from_status)
            .values(status=transition.to_status)
            .returning(SCANS.c.id)
        )
        moved = self._conn.execute(statement).scalar_one_or_none() is not None
        if moved:
            _record(self._conn, scan_id, transition, at)
        return moved

    def queued_before(self, at: datetime) -> list[uuid.UUID]:
        query = select(SCANS.c.id).where(
            SCANS.c.status == ScanStatus.QUEUED, SCANS.c.created_at < at
        )
        return list(self._conn.execute(query).scalars())

    def running_before(self, at: datetime) -> list[uuid.UUID]:
        claimed = (
            select(TRANSITIONS.c.scan_id)
            .where(TRANSITIONS.c.to_status == ScanStatus.RUNNING, TRANSITIONS.c.at < at)
            .scalar_subquery()
        )
        query = select(SCANS.c.id).where(
            SCANS.c.status == ScanStatus.RUNNING, SCANS.c.id.in_(claimed)
        )
        return list(self._conn.execute(query).scalars())


def _record(conn: Connection, scan_id: uuid.UUID, transition: Transition, at: datetime) -> None:
    conn.execute(
        insert(TRANSITIONS).values(
            id=uuid.uuid4(),
            scan_id=scan_id,
            from_status=transition.from_status,
            to_status=transition.to_status,
            actor=transition.actor,
            actor_user_id=transition.actor_user_id,
            reason=transition.reason,
            at=at,
        )
    )
