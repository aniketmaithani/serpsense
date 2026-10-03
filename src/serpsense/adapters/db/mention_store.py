"""Mentions, review revisions and mention observations in Postgres (data-model §5)."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Connection, Table, func, select, true, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from serpsense.adapters.db.models.mentions import Mention, MentionRevision
from serpsense.adapters.db.models.observations import MentionObservation
from serpsense.adapters.db.models.scans import Scan
from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import REVIEW_SOURCES, ParsedMention, best_ranked, normalised_text
from serpsense.ports.mention_store import Recorded, Sighting

MENTIONS = cast(Table, Mention.__table__)
REVISIONS = cast(Table, MentionRevision.__table__)
OBSERVATIONS = cast(Table, MentionObservation.__table__)
SCANS = cast(Table, Scan.__table__)
Key = tuple[MentionSource, str]


@dataclass(frozen=True)
class _Known:
    mention_id: uuid.UUID
    revision: int  # the latest; the mention's own text is revision 1
    text: str  # the latest revision's text


class SqlMentionStore:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record(self, sighting: Sighting, *, at: datetime) -> Recorded:
        if at.tzinfo is None:
            raise ValueError("record needs a timezone-aware time")
        seen = best_ranked(sighting.mentions)
        if not seen:
            return Recorded(new=0, revised=0, observed=0)
        brand_id = self._conn.execute(
            select(SCANS.c.brand_id).where(SCANS.c.id == sighting.scan_id)
        ).scalar_one()
        rows = [_mention_row(brand_id, sighting, mention, at) for mention in seen]
        inserted = (
            pg_insert(MENTIONS).values(rows).on_conflict_do_nothing().returning(MENTIONS.c.id)
        )
        new = set(self._conn.execute(inserted).scalars())
        known = self._known(brand_id, seen)
        revisions = [
            {
                "mention_id": known[_key(m)].mention_id,
                "revision": known[_key(m)].revision + 1,
                "text": m.text,
                "scan_id": sighting.scan_id,
                "created_at": at,
            }
            for m in seen
            if _edited(m, known[_key(m)])
        ]
        observations = [
            {
                "mention_id": known[_key(m)].mention_id,
                "scan_id": sighting.scan_id,
                "position": m.position,
                "star_rating": m.star_rating,
            }
            for m in seen
        ]
        return Recorded(
            new=len(new),
            # Only the retry rule (one new text per mention per scan) may skip a revision; a
            # clash on the revision number means a second writer and must raise.
            revised=self._insert(REVISIONS, revisions, ["mention_id", "scan_id"]),
            observed=self._insert(OBSERVATIONS, observations, ["mention_id", "scan_id"]),
        )

    def _known(self, brand_id: uuid.UUID, seen: Sequence[ParsedMention]) -> dict[Key, _Known]:
        latest = (
            select(REVISIONS.c.revision, REVISIONS.c.text)
            .where(REVISIONS.c.mention_id == MENTIONS.c.id)
            .order_by(REVISIONS.c.revision.desc())
            .limit(1)
            .lateral()
        )
        keys = [_key(mention) for mention in seen]
        query = (
            select(
                MENTIONS.c.source,
                MENTIONS.c.identity_key,
                MENTIONS.c.id,
                func.coalesce(latest.c.revision, 1),
                func.coalesce(latest.c.text, MENTIONS.c.text),
            )
            .select_from(MENTIONS.outerjoin(latest, true()))
            .where(
                MENTIONS.c.brand_id == brand_id,
                tuple_(MENTIONS.c.source, MENTIONS.c.identity_key).in_(keys),
            )
        )
        return {(row[0], row[1]): _Known(*row[2:]) for row in self._conn.execute(query)}

    def _insert(self, table: Table, rows: list[dict[str, Any]], key: list[str]) -> int:
        """Rows already there (a retried scan) are left alone; returns how many were new."""
        if not rows:
            return 0
        statement = pg_insert(table).values(rows).on_conflict_do_nothing(index_elements=key)
        return len(self._conn.execute(statement.returning(table.c.scan_id)).all())


def _mention_row(
    brand_id: uuid.UUID, sighting: Sighting, mention: ParsedMention, at: datetime
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "brand_id": brand_id,
        "source": mention.source,
        "identity_key": mention.identity_key,
        "text": mention.text,
        "url": mention.url,
        "outlet": mention.outlet,
        "brand_app_id": (
            sighting.brand_app_id if mention.source is MentionSource.PLAY_REVIEW else None
        ),
        "brand_location_id": (
            sighting.brand_location_id if mention.source is MentionSource.MAPS_REVIEW else None
        ),
        "language_code": mention.language_code,
        "published_at": mention.published_at,
        "created_at": at,
    }


def _key(mention: ParsedMention) -> Key:
    return (mention.source, mention.identity_key)


def _edited(mention: ParsedMention, known: _Known) -> bool:
    """Only a review's text can be edited; a snippet varies between queries instead. A mention
    first seen now was stored with this text, so it never counts as edited."""
    return mention.source in REVIEW_SOURCES and normalised_text(mention.text) != normalised_text(
        known.text
    )
