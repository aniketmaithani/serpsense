"""Alerts and in-app notifications in Postgres (data-model §8).

Levels come from `v_scan_scores`; an alert's brand is its scan's, and a notification about it
goes to that brand's owner. The stories a scan's grouping added to are the narratives of the
placements its grouping calls made; each counts the mentions now placed in it, per source.
"""

import uuid
from datetime import datetime
from types import MappingProxyType
from typing import cast

from sqlalchemy import Connection, Table, text
from sqlalchemy.dialects.postgresql import insert

from serpsense.adapters.db.models.alerts import Alert
from serpsense.domain.alert_rules import AlertFacts, Fired, Story
from serpsense.domain.enums import AlertRule, CrisisComponent, CrisisLevel, MentionSource
from serpsense.ports.alert_store import ScanAlertContext

ALERTS = cast(Table, Alert.__table__)
SCAN = text(
    """
SELECT s.brand_id, s.created_at, b.name, v.health, v.crisis, v.crisis_level,
       EXISTS (SELECT 1 FROM brand_competitors c WHERE c.competitor_brand_id = s.brand_id)
           AS competitor,
       coalesce((SELECT value FROM crisis_components
                 WHERE scan_id = s.id AND component = :autocomplete), 0) AS autocomplete
FROM scans s JOIN brands b ON b.id = s.brand_id JOIN v_scan_scores v ON v.scan_id = s.id
WHERE s.id = :scan
"""
)
PREVIOUS = text(
    """
SELECT crisis_level FROM v_scan_scores
WHERE brand_id = :brand AND created_at < :at ORDER BY created_at DESC LIMIT 1
"""
)
LAST_FIRED = text(
    """
SELECT DISTINCT ON (a.rule) a.rule, s.created_at, v.crisis_level
FROM alerts a JOIN scans s ON s.id = a.scan_id LEFT JOIN v_scan_scores v ON v.scan_id = s.id
WHERE s.brand_id = :brand AND s.created_at < :at AND a.narrative_id IS NULL
ORDER BY a.rule, s.created_at DESC
"""
)
STORIES = text(
    """
WITH grew AS (
    SELECT DISTINCT a.narrative_id
    FROM llm_calls c JOIN narrative_assignments a ON a.llm_call_id = c.id
    WHERE c.scan_id = :scan
), current AS (
    SELECT DISTINCT ON (a.mention_id) a.mention_id, a.narrative_id
    FROM narrative_assignments a JOIN narratives n ON n.id = a.narrative_id
    WHERE n.brand_id = :brand
    ORDER BY a.mention_id, a.created_at DESC
)
SELECT n.id, n.label, m.source, count(*) AS mentions,
       (SELECT max(s.created_at) FROM alerts al JOIN scans s ON s.id = al.scan_id
        WHERE al.narrative_id = n.id AND s.created_at < :at) AS last
FROM grew g
JOIN current c ON c.narrative_id = g.narrative_id
JOIN narratives n ON n.id = c.narrative_id
JOIN mentions m ON m.id = c.mention_id
GROUP BY n.id, n.label, m.source
ORDER BY n.id, m.source
"""
)
NOTIFY = text(
    """
INSERT INTO notifications (id, user_id, alert_id, title, body, created_at)
SELECT :id, b.owner_id, a.id, :title, :body, :at
FROM alerts a JOIN scans s ON s.id = a.scan_id JOIN brands b ON b.id = s.brand_id
WHERE a.id = :alert
ON CONFLICT DO NOTHING
RETURNING id
"""
)


class SqlAlertStore:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def context(self, scan_id: uuid.UUID) -> ScanAlertContext | None:
        values = {"scan": scan_id, "autocomplete": CrisisComponent.AUTOCOMPLETE}
        row = self._conn.execute(SCAN, values).first()
        if row is None:
            return None
        known = {"brand": row.brand_id, "at": row.created_at}
        previous = self._conn.execute(PREVIOUS, known).scalar_one_or_none()
        last = {
            AlertRule(r.rule): Fired(r.created_at, _level(r.crisis_level))
            for r in self._conn.execute(LAST_FIRED, known)
        }
        facts = AlertFacts(
            row.created_at,
            _level(row.crisis_level),
            _level(previous),
            row.autocomplete,
            last,
            self._stories(scan_id, row.brand_id, row.created_at),
        )
        return ScanAlertContext(facts, row.name, row.competitor, row.health, row.crisis)

    def _stories(self, scan_id: uuid.UUID, brand_id: uuid.UUID, at: datetime) -> tuple[Story, ...]:
        """The narratives the scan's grouping added to, with their current mentions per source
        and when the scan of their last alert was made (an alert on a narrative is a spread)."""
        rows = self._conn.execute(STORIES, {"scan": scan_id, "brand": brand_id, "at": at})
        sources: dict[uuid.UUID, dict[MentionSource, int]] = {}
        named: dict[uuid.UUID, tuple[str, datetime | None]] = {}
        for row in rows:
            sources.setdefault(row.id, {})[MentionSource(row.source)] = row.mentions
            named[row.id] = (row.label, row.last)
        return tuple(
            Story(story, named[story][0], MappingProxyType(counts), named[story][1])
            for story, counts in sources.items()
        )

    def fire(
        self,
        scan_id: uuid.UUID,
        rule: AlertRule,
        *,
        at: datetime,
        narrative_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        alert = {"id": uuid.uuid4(), "scan_id": scan_id, "rule": rule, "created_at": at}
        alert |= {"narrative_id": narrative_id}
        statement = insert(ALERTS).values(alert).on_conflict_do_nothing().returning(ALERTS.c.id)
        return self._conn.execute(statement).scalar_one_or_none()

    def notify(self, alert_id: uuid.UUID, *, title: str, body: str, at: datetime) -> bool:
        values = {"id": uuid.uuid4(), "alert": alert_id, "title": title, "body": body, "at": at}
        return self._conn.execute(NOTIFY, values).first() is not None


def _level(value: str | None) -> CrisisLevel | None:
    return None if value is None else CrisisLevel(value)
