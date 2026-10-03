"""Narratives and their mentions in Postgres (data-model §6).

A mention's label is its current one (`adapters/db/labels.py`), the same the scores read, and
its text its latest revision's.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, func, or_, select, text
from sqlalchemy.dialects.postgresql import distinct_on

from serpsense.adapters.db import labels
from serpsense.adapters.db.models.llm import NarrativeAssignment
from serpsense.domain.enums import LlmCallOutcome, LlmTask, MentionSource, Topic
from serpsense.ports.narrative_store import UngroupedMention

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
