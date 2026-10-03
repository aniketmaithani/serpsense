"""Alert briefs and explanations in Postgres (data-model §6, §8): scores from `v_scan_scores`,
each mention's latest text and the newest label for it (the active prompt's first)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, TextClause, text

from serpsense.adapters.db.overview import ACTIVE_PROMPTS
from serpsense.domain.enums import AlertRule, CrisisComponent, CrisisLevel, MentionSource
from serpsense.ports.explanations import AlertBrief, BriefMention

MOST_MENTIONS = 15
ALERT = text(
    """
SELECT a.id, a.scan_id, a.rule, a.narrative_id, b.owner_id, b.name,
       v.health, v.crisis, v.crisis_level,
       EXISTS (SELECT 1 FROM brand_competitors c WHERE c.competitor_brand_id = b.id) AS competitor,
       (SELECT p.crisis_level FROM v_scan_scores p
        WHERE p.brand_id = s.brand_id AND p.created_at < s.created_at
        ORDER BY p.created_at DESC LIMIT 1) AS previous_level,
       n.label, n.summary,
       EXISTS (SELECT 1 FROM alert_explanations e WHERE e.alert_id = a.id) AS explained,
       (b.archived_at IS NOT NULL OR u.deleted_at IS NOT NULL) AS gone
FROM alerts a JOIN scans s ON s.id = a.scan_id JOIN brands b ON b.id = s.brand_id
JOIN users u ON u.id = b.owner_id
LEFT JOIN v_scan_scores v ON v.scan_id = s.id
LEFT JOIN narratives n ON n.id = a.narrative_id
WHERE a.id = :alert
"""
)
COMPONENTS = text("SELECT component, value FROM crisis_components WHERE scan_id = :scan")
LABELLED = """
SELECT m.source, coalesce(rv.text, m.text) AS text, e.sentiment
FROM mentions m
LEFT JOIN LATERAL (SELECT revision, text FROM mention_revisions WHERE mention_id = m.id
                   ORDER BY revision DESC LIMIT 1) rv ON true
LEFT JOIN LATERAL (SELECT sentiment, severity, is_about_brand FROM enrichments
                   WHERE mention_id = m.id AND revision = coalesce(rv.revision, 1)
                   ORDER BY prompt_version = ANY(:active) DESC, created_at DESC, id DESC
                   LIMIT 1) e ON true
"""


def _labelled(where: str) -> TextClause:
    """Mentions with their latest text and label, filtered and ordered by `where`."""
    return text("".join((LABELLED, where)))


# A spreading story: its current mentions (those whose latest assignment is the story).
STORY_MENTIONS = _labelled(
    """
WHERE m.id IN (SELECT mention_id FROM narrative_assignments WHERE narrative_id = :story)
  AND (SELECT y.narrative_id FROM narrative_assignments y WHERE y.mention_id = m.id
       ORDER BY y.created_at DESC, y.id DESC LIMIT 1) = :story
ORDER BY e.sentiment ASC NULLS LAST, e.severity DESC NULLS LAST, m.id
LIMIT :limit
"""
)
# Any other alert: the scan's unfavourable mentions about the brand (autocomplete only, for a
# negative suggestion).
SCAN_MENTIONS = _labelled(
    """
JOIN mention_observations o ON o.mention_id = m.id AND o.scan_id = :scan
WHERE e.sentiment = -1 AND e.is_about_brand
  AND (CAST(:source AS text) IS NULL OR m.source::text = :source)
ORDER BY e.severity DESC, m.id
LIMIT :limit
"""
)
RECORD = text(
    """
INSERT INTO alert_explanations (id, alert_id, text, prompt_version, llm_call_id, created_at)
VALUES (:id, :alert, :text, :prompt, :call, :at)
ON CONFLICT (alert_id) DO NOTHING
RETURNING id
"""
)


class SqlExplanationStore:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def brief(self, alert_id: uuid.UUID) -> AlertBrief | None:
        row = self._conn.execute(ALERT, {"alert": alert_id}).first()
        if row is None:
            return None
        rule = AlertRule(row.rule)
        components = {
            CrisisComponent(c.component): c.value
            for c in self._conn.execute(COMPONENTS, {"scan": row.scan_id})
        }
        return AlertBrief(
            alert_id=row.id,
            scan_id=row.scan_id,
            owner_id=row.owner_id,
            brand_name=row.name,
            competitor=row.competitor,
            rule=rule,
            level=_level(row.crisis_level),
            previous_level=_level(row.previous_level),
            crisis=row.crisis or 0,
            health=row.health,
            components=components,
            story_label=row.label,
            story_summary=row.summary,
            mentions=self._mentions(row.scan_id, row.narrative_id, rule),
            explained=row.explained,
            gone=row.gone,
        )

    def record(
        self,
        alert_id: uuid.UUID,
        *,
        text: str,
        prompt_version: str,
        llm_call_id: uuid.UUID,
        at: datetime,
    ) -> bool:
        values = {"id": uuid.uuid4(), "alert": alert_id, "text": text, "prompt": prompt_version}
        added = self._conn.execute(RECORD, values | {"call": llm_call_id, "at": at})
        return added.first() is not None

    def _mentions(
        self, scan_id: uuid.UUID, story: uuid.UUID | None, rule: AlertRule
    ) -> tuple[BriefMention, ...]:
        known: dict[str, Any] = {"active": ACTIVE_PROMPTS, "limit": MOST_MENTIONS}
        if story is not None:
            rows = self._conn.execute(STORY_MENTIONS, known | {"story": story})
        else:
            negative = rule is AlertRule.NEW_NEGATIVE_AUTOCOMPLETE
            only = MentionSource.AUTOCOMPLETE if negative else None
            rows = self._conn.execute(SCAN_MENTIONS, known | {"scan": scan_id, "source": only})
        return tuple(
            BriefMention(source=MentionSource(r.source), text=r.text, sentiment=r.sentiment)
            for r in rows
        )


def _level(value: str | None) -> CrisisLevel | None:
    return None if value is None else CrisisLevel(value)
