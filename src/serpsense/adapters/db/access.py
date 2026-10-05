"""Who may sign up, in Postgres (data-model §1, ADR-0014, ADR-0015): one mutable row per address
that asked, an append-only decision log whose latest row counts, and the operator's sign-up mode
switches, likewise."""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, func, select
from sqlalchemy.dialects.postgresql import insert as upsert

from serpsense.adapters.db.models.access import (
    AccessRequest,
    AccessRequestDecision,
    SignupModeChange,
)
from serpsense.domain.enums import AccessDecision, SignupMode
from serpsense.ports.access import ModeSwitch

REQUESTS = cast(Table, AccessRequest.__table__)
DECISIONS = cast(Table, AccessRequestDecision.__table__)
SWITCHES = cast(Table, SignupModeChange.__table__)


class SqlAccessRequests:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record(self, email: str, *, at: datetime) -> None:
        statement = upsert(REQUESTS).values(id=uuid.uuid4(), email=email, requested_at=at)
        self._conn.execute(statement.on_conflict_do_nothing(index_elements=["email"]))

    def recorded_between(self, start: datetime, end: datetime) -> int:
        query = select(func.count()).where(REQUESTS.c.requested_at.between(start, end))
        return self._conn.execute(query).scalar_one()

    def approved(self, email: str) -> bool:
        latest = (
            select(DECISIONS.c.decision)
            .join(REQUESTS, REQUESTS.c.id == DECISIONS.c.access_request_id)
            .where(REQUESTS.c.email == email)  # citext: ignores case
            .order_by(DECISIONS.c.decided_at.desc())
            .limit(1)
        )
        return self._conn.execute(latest).scalar_one_or_none() == AccessDecision.APPROVED

    def admit(self, email: str, *, at: datetime) -> None:
        self.record(email, at=at)
        if self.approved(email):
            return
        mine = select(REQUESTS.c.id).where(REQUESTS.c.email == email)  # citext: ignores case
        self.decide(self._conn.execute(mine).scalar_one(), AccessDecision.APPROVED, at=at)

    def decide(self, request_id: uuid.UUID, decision: AccessDecision, *, at: datetime) -> bool:
        known = select(REQUESTS.c.id).where(REQUESTS.c.id == request_id)
        if self._conn.execute(known).first() is None:
            return False
        values = {"id": uuid.uuid4(), "access_request_id": request_id, "decided_at": at}
        statement = upsert(DECISIONS).values(decision=decision, **values)
        self._conn.execute(statement.on_conflict_do_nothing())
        return True

    def signup_mode(self) -> ModeSwitch | None:
        latest = select(SWITCHES.c.mode, SWITCHES.c.changed_at).order_by(
            SWITCHES.c.changed_at.desc()
        )
        row = self._conn.execute(latest.limit(1)).first()
        return None if row is None else ModeSwitch(SignupMode(row.mode), row.changed_at)

    def switch_signup_mode(self, mode: SignupMode, *, at: datetime) -> None:
        statement = upsert(SWITCHES).values(id=uuid.uuid4(), mode=mode, changed_at=at)
        self._conn.execute(statement.on_conflict_do_nothing())
