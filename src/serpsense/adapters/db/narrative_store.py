"""Narratives and their mentions in Postgres (data-model §6).

A mention's label is its current one (`adapters/db/labels.py`), the same the scores read, and
its text its latest revision's; its current narrative is its latest assignment.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, func, or_, select, text
from sqlalchemy.dialects.postgresql import distinct_on, insert

from serpsense.adapters.db import labels
from serpsense.adapters.db.models.llm import Narrative, NarrativeAssignment
from serpsense.domain.enums import LlmCallOutcome, LlmTask, MentionSource, Topic
from serpsense.ports.narrative_store import (
    Assignment,
    NewNarrative,
    OpenNarrative,
    UngroupedMention,
)

NARRATIVES = cast(Table, Narrative.__table__)
ASSIGNMENTS = cast(Table, NarrativeAssignment.__table__)
ENRICHMENTS, MENTIONS, REVISIONS = labels.ENRICHMENTS, labels.MENTIONS, labels.REVISIONS
LAST_GROUPING = text(
    """
SELECT c.created_at, c.outcome
FROM llm_calls c JOIN scans s ON s.id = c.scan_id
WHERE s.brand_id = :brand AND c.task = 'group_narratives'
ORDER BY c.created_at DESC, c.id
LIMIT 1
"""
)
OPEN = text(
    """
WITH current AS (
    SELECT DISTINCT ON (a.mention_id) a.mention_id, a.narrative_id, a.created_at
    FROM narrative_assignments a JOIN narratives n ON n.id = a.narrative_id
    WHERE n.brand_id = :brand
    ORDER BY a.mention_id, a.created_at DESC
)
SELECT n.id, n.label, n.summary, count(*) AS mentions
FROM narratives n JOIN current c ON c.narrative_id = n.id
GROUP BY n.id
HAVING max(c.created_at) >= :since
ORDER BY max(c.created_at) DESC, n.id
LIMIT :limit
"""
)


class SqlNarrativeStore:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def ungrouped(
        self,
        brand_id: uuid.UUID,
        *,
        first_seen_since: datetime,
        prompts: Mapping[LlmTask, str],
        limit: int,
    ) -> list[UngroupedMention]:
        latest_text = (
            select(REVISIONS.c.text)
            .where(REVISIONS.c.mention_id == MENTIONS.c.id)
            .order_by(REVISIONS.c.revision.desc())
            .limit(1)
            .scalar_subquery()
        )
        current = (
            select(
                MENTIONS.c.id,
                MENTIONS.c.source,
                MENTIONS.c.language_code,
                func.coalesce(latest_text, MENTIONS.c.text).label("text"),
                MENTIONS.c.created_at,
                ENRICHMENTS.c.topic,
                ENRICHMENTS.c.severity,
                ENRICHMENTS.c.sentiment,
                ENRICHMENTS.c.is_complaint,
                ENRICHMENTS.c.is_about_brand,
                ENRICHMENTS.c.created_at.label("labelled_at"),
            )
            .join(ENRICHMENTS, ENRICHMENTS.c.mention_id == MENTIONS.c.id)
            .where(
                MENTIONS.c.brand_id == brand_id,
                MENTIONS.c.created_at >= first_seen_since,
                *labels.conditions(),
            )
            .order_by(MENTIONS.c.id, *labels.preference(prompts))
            .ext(distinct_on(MENTIONS.c.id))
            .subquery()
        )
        placed = select(ASSIGNMENTS.c.id).where(ASSIGNMENTS.c.mention_id == current.c.id).exists()
        query = (
            select(current)
            .where(
                current.c.is_about_brand,
                or_(current.c.sentiment == -1, current.c.is_complaint),
                ~placed,
            )
            .order_by(current.c.created_at.desc(), current.c.id)  # newest first, stable
            .limit(limit)
        )
        return [
            UngroupedMention(
                mention_id=row.id,
                source=MentionSource(row.source),
                language_code=row.language_code,
                text=row.text,
                topic=Topic(row.topic),
                severity=row.severity,
                labelled_at=row.labelled_at,
            )
            for row in self._conn.execute(query)
        ]

    def considered_until(self, brand_id: uuid.UUID) -> datetime | None:
        row = self._conn.execute(LAST_GROUPING, {"brand": brand_id}).first()
        succeeded = row is not None and row.outcome == LlmCallOutcome.SUCCEEDED
        return row.created_at if row is not None and succeeded else None

    def open(
        self, brand_id: uuid.UUID, *, active_since: datetime, limit: int
    ) -> list[OpenNarrative]:
        values = {"brand": brand_id, "since": active_since, "limit": limit}
        return [OpenNarrative(*row) for row in self._conn.execute(OPEN, values)]

    def record(
        self,
        narratives: Sequence[NewNarrative],
        assignments: Sequence[Assignment],
        *,
        prompt_version: str,
        llm_call_id: uuid.UUID,
        at: datetime,
    ) -> int:
        mentions = [a.mention_id for a in assignments]
        placed = select(ASSIGNMENTS.c.mention_id).where(ASSIGNMENTS.c.mention_id.in_(mentions))
        taken = set(self._conn.execute(placed).scalars()) if mentions else set()
        fresh = [a for a in assignments if a.mention_id not in taken]
        joined = {a.narrative_id for a in fresh}
        stories = [n for n in narratives if n.narrative_id in joined]
        made = {"prompt_version": prompt_version, "llm_call_id": llm_call_id, "created_at": at}
        if stories:
            rows = [
                {
                    "id": n.narrative_id,
                    "brand_id": n.brand_id,
                    "label": n.label,
                    "summary": n.summary,
                }
                | made
                for n in stories
            ]
            self._conn.execute(insert(NARRATIVES).values(rows))
        if not fresh:
            return 0
        values = [
            {"id": uuid.uuid4(), "mention_id": a.mention_id, "narrative_id": a.narrative_id} | made
            for a in fresh
        ]
        # Only one scan of a brand is active, so nothing places these mentions meanwhile.
        statement = insert(ASSIGNMENTS).values(values).on_conflict_do_nothing()
        return len(self._conn.execute(statement.returning(ASSIGNMENTS.c.id)).all())
