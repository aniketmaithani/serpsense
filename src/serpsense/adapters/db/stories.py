"""The narratives a brand's pages show, read from Postgres (data-model §6): each mention's latest
assignment, scoped through the owner check like every page read."""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import Connection

from serpsense.adapters.db.overview import ACTIVE_PROMPTS
from serpsense.adapters.db.scoping import scoped
from serpsense.domain.enums import MentionSource
from serpsense.ports.overview import MentionRow
from serpsense.ports.stories import Story, StoryRow

STORY_MENTIONS = 50
BRAND = scoped("SELECT name FROM brands WHERE id IN ({owned}) AND id = :brand")
CURRENT = """
WITH current AS (
  SELECT DISTINCT ON (a.mention_id) a.mention_id, a.narrative_id, a.created_at
  FROM narrative_assignments a JOIN mentions m ON m.id = a.mention_id
  WHERE m.brand_id IN ({owned}) AND m.brand_id = :brand
  ORDER BY a.mention_id, a.created_at DESC, a.id DESC
)"""
ROWS = scoped(
    CURRENT,
    """
SELECT n.id, n.label, n.summary, n.prompt_version, count(*) AS mentions,
       array_agg(DISTINCT m.source::text ORDER BY m.source::text) AS sources,
       max(c.created_at) AS last_at
FROM narratives n JOIN current c ON c.narrative_id = n.id JOIN mentions m ON m.id = c.mention_id
WHERE n.brand_id = :brand AND (CAST(:story AS uuid) IS NULL OR n.id = :story)
GROUP BY n.id
ORDER BY count(*) DESC, max(c.created_at) DESC, n.id
LIMIT :limit
""",
)
MENTIONS = scoped(
    CURRENT,
    """
SELECT m.source, coalesce(rv.text, m.text) AS text, m.url, m.outlet, m.published_at,
       e.sentiment, e.reason
FROM current c JOIN mentions m ON m.id = c.mention_id
LEFT JOIN LATERAL (SELECT revision, text FROM mention_revisions WHERE mention_id = m.id
                   ORDER BY revision DESC LIMIT 1) rv ON true
LEFT JOIN LATERAL (SELECT sentiment, severity, reason FROM enrichments
                   WHERE mention_id = m.id AND revision = coalesce(rv.revision, 1)
                   ORDER BY prompt_version = ANY(:active) DESC, created_at DESC, id DESC
                   LIMIT 1) e ON true
WHERE c.narrative_id = :story
ORDER BY e.sentiment ASC NULLS LAST, e.severity DESC NULLS LAST, m.published_at DESC NULLS LAST,
         m.id
LIMIT :limit
""",
)


class SqlStories:
    def __init__(self, connect: Callable[[], AbstractContextManager[Connection]]) -> None:
        self._connect = connect

    def of_brand(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, *, limit: int
    ) -> list[StoryRow] | None:
        known = {"user": user_id, "brand": brand_id}
        with self._connect() as conn:
            if conn.execute(BRAND, known).first() is None:
                return None
            rows = conn.execute(ROWS, known | {"story": None, "limit": limit})
            return [_row(row) for row in rows]

    def story(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, narrative_id: uuid.UUID
    ) -> Story | None:
        known = {"user": user_id, "brand": brand_id, "story": narrative_id}
        with self._connect() as conn:
            name = conn.execute(BRAND, known).scalar_one_or_none()
            row = conn.execute(ROWS, known | {"limit": 1}).first()
            if name is None or row is None:
                return None
            more = {"active": ACTIVE_PROMPTS, "limit": STORY_MENTIONS}
            mentions = tuple(map(_mention, conn.execute(MENTIONS, known | more)))
        return Story(brand_id=brand_id, brand_name=name, row=_row(row), mentions=mentions)


def _row(row: Any) -> StoryRow:
    return StoryRow(
        narrative_id=row.id,
        label=row.label,
        summary=row.summary,
        prompt_version=row.prompt_version,
        mentions=row.mentions,
        sources=tuple(MentionSource(s) for s in row.sources),
        last_grouped_at=row.last_at,
    )


def _mention(row: Any) -> MentionRow:
    return MentionRow(
        source=MentionSource(row.source),
        text=row.text,
        url=row.url,
        outlet=row.outlet,
        position=None,
        published_at=row.published_at,
        sentiment=row.sentiment,
        reason=row.reason,
    )
