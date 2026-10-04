"""The audit log in Postgres (data-model §9)."""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, func, select
from sqlalchemy.dialects.postgresql import insert

from serpsense.adapters.db.models.audit import AuditEvent, AuditEventNetwork
from serpsense.domain.auth import clean_user_agent
from serpsense.domain.enums import AuditAction
from serpsense.ports.audit import AuditEntry

EVENTS, NETWORK = cast(Table, AuditEvent.__table__), cast(Table, AuditEventNetwork.__table__)
# No actor leads ix_audit_events_actor_user_id_created_at, so these reads use it.
OPERATOR = EVENTS.c.actor_user_id.is_(None)


class SqlAuditLog:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record(self, entry: AuditEntry) -> uuid.UUID:
        event_id = uuid.uuid4()
        target_type, target_id = entry.target if entry.target else (None, None)
        event = {
            "id": event_id,
            "actor_user_id": entry.actor_user_id,
            "action": entry.action,
            "target_type": target_type,
            "target_id": target_id,
            "details": dict(entry.details) if entry.details else None,
            "created_at": entry.at,
        }
        self._conn.execute(insert(EVENTS).values(event))
        network = entry.network
        agent = clean_user_agent(network.user_agent) if network else None
        if network is not None and (network.ip or agent):
            row = {"audit_event_id": event_id, "ip": network.ip, "user_agent": agent}
            self._conn.execute(insert(NETWORK).values(row))
        return event_id

    def operator_events_since(self, action: AuditAction, since: datetime) -> int:
        query = select(func.count()).where(
            OPERATOR, EVENTS.c.action == action, EVENTS.c.created_at >= since
        )
        return self._conn.execute(query).scalar_one()

    def last_operator_event(self, action: AuditAction) -> datetime | None:
        query = select(func.max(EVENTS.c.created_at)).where(OPERATOR, EVENTS.c.action == action)
        return cast(datetime | None, self._conn.execute(query).scalar_one())
