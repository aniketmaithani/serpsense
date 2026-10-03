"""The notifications centre on Postgres (data-model §8): reads and read marks for one user."""

import uuid
from typing import Any

from sqlalchemy import Engine, text

from serpsense.adapters.db.scoping import scoped
from serpsense.ports.clock import Clock
from serpsense.ports.inbox import NotificationRow

LATEST = scoped(
    """
SELECT n.id, n.title, n.body, n.created_at, r.read_at IS NOT NULL AS read, s.brand_id,
       e.text AS explanation
FROM notifications n
LEFT JOIN notification_reads r ON r.notification_id = n.id
LEFT JOIN alerts a ON a.id = n.alert_id
LEFT JOIN alert_explanations e ON e.alert_id = n.alert_id
LEFT JOIN scans s ON s.id = a.scan_id AND s.brand_id IN ({owned})
WHERE n.user_id = :user
ORDER BY n.created_at DESC, n.id DESC
LIMIT :limit
"""
)
UNREAD = text(
    """
SELECT count(*) FROM notifications n
WHERE n.user_id = :user
  AND NOT EXISTS (SELECT 1 FROM notification_reads r WHERE r.notification_id = n.id)
"""
)
MARK_READ = text(
    """
INSERT INTO notification_reads (notification_id, read_at)
SELECT n.id, :at FROM notifications n
JOIN notifications pick ON pick.id = :one AND pick.user_id = :user
WHERE n.user_id = :user
  AND (n.id = pick.id OR (:and_older AND (n.created_at, n.id) < (pick.created_at, pick.id)))
ON CONFLICT DO NOTHING
"""
)


class SqlInbox:
    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine, self._clock = engine, clock

    def latest(self, user_id: uuid.UUID, *, limit: int) -> list[NotificationRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(LATEST, {"user": user_id, "limit": limit})
            return [_row(row) for row in rows]

    def unread(self, user_id: uuid.UUID) -> int:
        with self._engine.connect() as conn:
            return int(conn.execute(UNREAD, {"user": user_id}).scalar_one())

    def mark_read(
        self, user_id: uuid.UUID, notification_id: uuid.UUID, *, and_older: bool = False
    ) -> int:
        values = {"user": user_id, "one": notification_id, "and_older": and_older}
        values["at"] = self._clock.now()
        with self._engine.begin() as conn:
            return conn.execute(MARK_READ, values).rowcount


def _row(row: Any) -> NotificationRow:
    return NotificationRow(
        notification_id=row.id,
        title=row.title,
        body=row.body,
        at=row.created_at,
        read=row.read,
        brand_id=row.brand_id,
        explanation=row.explanation,
    )
