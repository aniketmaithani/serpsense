"""The email outbox in Postgres (data-model §8): claims with FOR UPDATE SKIP LOCKED, and the
status written only with an attempt row, by the rule in `domain/outbox.py`."""

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, select, text, update
from sqlalchemy.dialects.postgresql import insert

from serpsense.adapters.db.models.outbox import OutboxAttempt, OutboxMessage
from serpsense.domain import outbox as rules
from serpsense.domain.enums import OutboxKind, OutboxOutcome, OutboxStatus
from serpsense.ports.outbox import DueEmail

MESSAGES = cast(Table, OutboxMessage.__table__)
ATTEMPTS = cast(Table, OutboxAttempt.__table__)
# The row stays locked through the send; let the session idle that long (ADR-0010).
SEND_WINDOW = text("SET LOCAL idle_in_transaction_session_timeout = '3min'")
ALERT_EMAIL = text(
    """
INSERT INTO outbox_messages (id, kind, user_id, alert_id, recipient_email, template, template_data,
                             dedupe_key, status, next_attempt_at, created_at)
SELECT :id, 'alert_email', u.id, a.id, u.email, 'alert', CAST(:data AS jsonb),
       :key, 'pending', :at, :at
FROM alerts a JOIN scans s ON s.id = a.scan_id JOIN brands b ON b.id = s.brand_id
JOIN users u ON u.id = b.owner_id
WHERE a.id = :alert AND u.deleted_at IS NULL
ON CONFLICT (dedupe_key) DO NOTHING
RETURNING id
"""
)
CLAIM = text(
    """
SELECT m.id, m.kind, m.recipient_email, m.template, m.template_data
FROM outbox_messages m
WHERE m.status = 'pending' AND m.next_attempt_at <= :at
ORDER BY m.next_attempt_at, m.id
LIMIT 1
FOR UPDATE OF m SKIP LOCKED
"""
)


class SqlOutbox:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add_alert_email(
        self, alert_id: uuid.UUID, *, data: Mapping[str, str], at: datetime
    ) -> bool:
        values = {"id": uuid.uuid4(), "alert": alert_id, "data": json.dumps(dict(data)), "at": at}
        added = self._conn.execute(ALERT_EMAIL, values | {"key": rules.alert_email_key(alert_id)})
        return added.first() is not None

    def claim_due(self, at: datetime) -> DueEmail | None:
        self._conn.execute(SEND_WINDOW)
        row = self._conn.execute(CLAIM, {"at": at}).first()
        if row is None:
            return None
        data = {str(k): str(v) for k, v in (row.template_data or {}).items()}
        kind = OutboxKind(row.kind)
        return DueEmail(row.id, kind, row.recipient_email, row.template, data)

    def record(
        self,
        message_id: uuid.UUID,
        outcome: OutboxOutcome,
        *,
        at: datetime,
        error_code: str | None = None,
    ) -> OutboxStatus:
        earlier = self._conn.execute(
            select(ATTEMPTS.c.outcome)
            .where(ATTEMPTS.c.outbox_message_id == message_id)
            .order_by(ATTEMPTS.c.attempted_at, ATTEMPTS.c.id)
        ).scalars()
        outcomes = [*(OutboxOutcome(o) for o in earlier), outcome]
        attempt = {"id": uuid.uuid4(), "outbox_message_id": message_id, "attempted_at": at}
        self._conn.execute(
            insert(ATTEMPTS).values(outcome=outcome, error_code=error_code, **attempt)
        )
        status = rules.status(outcomes)
        changes: dict[str, object] = {"status": status}
        if status is OutboxStatus.PENDING:
            retries = outcomes.count(OutboxOutcome.RETRYABLE_ERROR)
            changes["next_attempt_at"] = rules.next_attempt(at, retries)
        else:
            changes["sensitive_data_encrypted"] = None  # never kept past pending
        pending = (MESSAGES.c.id == message_id) & (MESSAGES.c.status == OutboxStatus.PENDING)
        changed = self._conn.execute(
            update(MESSAGES).where(pending).values(changes).returning(MESSAGES.c.id)
        )
        if changed.first() is None:
            raise LookupError("only a pending outbox message takes an attempt")
        return status
