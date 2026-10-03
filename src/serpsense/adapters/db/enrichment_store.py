"""Mention labels in Postgres: what still needs labelling, and the labels (data-model §6)."""

import uuid
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, func, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert

from serpsense.adapters.db.models.llm import Enrichment
from serpsense.adapters.db.models.mentions import Mention, MentionRevision
from serpsense.adapters.db.models.observations import MentionObservation
from serpsense.adapters.db.models.scans import Scan
from serpsense.domain.enums import MentionSource
from serpsense.ports.enrichment_store import MentionLabel, PendingText

ENRICHMENTS = cast(Table, Enrichment.__table__)
MENTIONS = cast(Table, Mention.__table__)
REVISIONS = cast(Table, MentionRevision.__table__)
OBSERVATIONS = cast(Table, MentionObservation.__table__)
SCANS = cast(Table, Scan.__table__)


class SqlEnrichmentStore:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def pending(
        self,
        brand_id: uuid.UUID,
        *,
        prompt_version: str,
        sources: Collection[MentionSource],
        seen_since: datetime,
        limit: int,
    ) -> list[PendingText]:
        # Start from the brand's recent scans, so the brand's whole history is never sorted.
        recent = (
            select(OBSERVATIONS.c.mention_id)
            .join(SCANS, SCANS.c.id == OBSERVATIONS.c.scan_id)
            .where(SCANS.c.brand_id == brand_id, SCANS.c.created_at >= seen_since)
            .distinct()
            .subquery()
        )
        latest = (
            select(REVISIONS.c.revision, REVISIONS.c.text)
            .where(REVISIONS.c.mention_id == MENTIONS.c.id)
            .order_by(REVISIONS.c.revision.desc())
            .limit(1)
            .lateral()
        )
        revision = func.coalesce(latest.c.revision, 1)
        labelled = (
            select(ENRICHMENTS.c.id)
            .where(
                ENRICHMENTS.c.mention_id == MENTIONS.c.id,
                ENRICHMENTS.c.revision == revision,
                ENRICHMENTS.c.prompt_version == prompt_version,
            )
            .exists()
        )
        query = (
            select(
                MENTIONS.c.id,
                revision.label("revision"),
                MENTIONS.c.source,
                MENTIONS.c.language_code,
                func.coalesce(latest.c.text, MENTIONS.c.text).label("text"),
            )
            .select_from(
                recent.join(MENTIONS, MENTIONS.c.id == recent.c.mention_id).outerjoin(
                    latest, true()
                )
            )
            .where(MENTIONS.c.source.in_(list(sources)), ~labelled)
            .order_by(MENTIONS.c.created_at.desc(), MENTIONS.c.id)  # newest first, stable
            .limit(limit)
        )
        return [PendingText(*row) for row in self._conn.execute(query)]

    def record(
        self,
        labels: Sequence[MentionLabel],
        *,
        prompt_version: str,
        llm_call_id: uuid.UUID,
        at: datetime,
    ) -> int:
        if not labels:
            return 0
        rows = [
            {
                "id": uuid.uuid4(),
                "mention_id": label.mention_id,
                "revision": label.revision,
                "prompt_version": prompt_version,
                "llm_call_id": llm_call_id,
                "sentiment": label.sentiment,
                "severity": label.severity,
                "topic": label.topic,
                "is_complaint": label.is_complaint,
                "is_about_brand": label.is_about_brand,
                "reason": label.reason,
                "created_at": at,
            }
            for label in labels
        ]
        # A label already there (a retried scan) stays: labels are never rewritten.
        statement = (
            pg_insert(ENRICHMENTS)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["mention_id", "revision", "prompt_version"])
        )
        return len(self._conn.execute(statement.returning(ENRICHMENTS.c.id)).all())
