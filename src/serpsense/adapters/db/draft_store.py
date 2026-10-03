"""Response drafts in Postgres (data-model §6): read through the one owner check, written with
their citations in the caller's transaction."""

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import Connection, text

from serpsense.adapters.db.overview import ACTIVE_PROMPTS
from serpsense.adapters.db.scoping import scoped
from serpsense.domain.enums import DraftKind, DraftPreset, MentionSource
from serpsense.ports.drafts import DraftMaterial, DraftRow, NewDraft, SourceMention

STORY = scoped(
    """
SELECT b.owner_id, b.name, n.label, n.summary
FROM narratives n JOIN brands b ON b.id = n.brand_id
WHERE n.id = :story AND n.brand_id = :brand AND b.id IN ({owned})
"""
)
MENTIONS = text(
    """
SELECT m.id, m.source, coalesce(rv.text, m.text) AS text, m.url, e.sentiment
FROM mentions m
LEFT JOIN LATERAL (SELECT revision, text FROM mention_revisions WHERE mention_id = m.id
                   ORDER BY revision DESC LIMIT 1) rv ON true
LEFT JOIN LATERAL (SELECT sentiment, severity FROM enrichments
                   WHERE mention_id = m.id AND revision = coalesce(rv.revision, 1)
                   ORDER BY prompt_version = ANY(:active) DESC, created_at DESC, id DESC
                   LIMIT 1) e ON true
WHERE m.id IN (SELECT mention_id FROM narrative_assignments WHERE narrative_id = :story)
  AND (SELECT y.narrative_id FROM narrative_assignments y WHERE y.mention_id = m.id
       ORDER BY y.created_at DESC, y.id DESC LIMIT 1) = :story
ORDER BY e.sentiment ASC NULLS LAST, e.severity DESC NULLS LAST, m.id
LIMIT :limit
"""
)
DRAFTS = scoped(
    """
SELECT d.id, d.kind, d.preset, d.text, d.reasoning_summary, d.prompt_version, d.created_at
FROM drafts d JOIN narratives n ON n.id = d.narrative_id
WHERE d.narrative_id = :story AND n.brand_id = :brand AND n.brand_id IN ({owned})
ORDER BY d.created_at DESC, d.id DESC
LIMIT :limit
"""
)
CITED = text(
    """
SELECT c.draft_id, m.id, m.source, coalesce(rv.text, m.text) AS text, m.url
FROM draft_citations c JOIN mentions m ON m.id = c.mention_id
LEFT JOIN LATERAL (SELECT text FROM mention_revisions WHERE mention_id = m.id
                   ORDER BY revision DESC LIMIT 1) rv ON true
WHERE c.draft_id = ANY(:drafts)
ORDER BY m.id
"""
)
ADD_DRAFT = text(
    """
INSERT INTO drafts (id, narrative_id, kind, preset, text, reasoning_summary, prompt_version,
                    llm_call_id, created_at)
VALUES (:id, :story, :kind, :preset, :text, :summary, :prompt, :call, :at)
"""
)
ADD_CITATION = text(
    "INSERT INTO draft_citations (draft_id, mention_id) VALUES (:draft, :mention) "
    "ON CONFLICT DO NOTHING"
)


class SqlDraftStore:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def material(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, narrative_id: uuid.UUID, *, limit: int
    ) -> DraftMaterial | None:
        known = {"user": user_id, "brand": brand_id, "story": narrative_id}
        story = self._conn.execute(STORY, known).first()
        if story is None:
            return None
        values = {"story": narrative_id, "active": ACTIVE_PROMPTS, "limit": limit}
        mentions = tuple(_mention(row) for row in self._conn.execute(MENTIONS, values))
        return DraftMaterial(
            owner_id=story.owner_id,
            brand_name=story.name,
            story_label=story.label,
            story_summary=story.summary,
            mentions=mentions,
        )

    def record(self, draft: NewDraft) -> uuid.UUID:
        draft_id = uuid.uuid4()
        values: dict[str, object] = {
            "id": draft_id,
            "story": draft.narrative_id,
            "kind": draft.kind.value,
            "preset": draft.preset.value,
            "text": draft.text,
            "summary": draft.reasoning_summary,
            "prompt": draft.prompt_version,
            "call": draft.llm_call_id,
            "at": draft.created_at,
        }
        self._conn.execute(ADD_DRAFT, values)
        for mention_id in draft.cited:
            self._conn.execute(ADD_CITATION, {"draft": draft_id, "mention": mention_id})
        return draft_id

    def drafts(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, narrative_id: uuid.UUID, *, limit: int
    ) -> list[DraftRow]:
        known = {"user": user_id, "brand": brand_id, "story": narrative_id, "limit": limit}
        rows = list(self._conn.execute(DRAFTS, known))
        cited: dict[uuid.UUID, list[SourceMention]] = defaultdict(list)
        if rows:
            for row in self._conn.execute(CITED, {"drafts": [r.id for r in rows]}):
                cited[row.draft_id].append(_mention(row))
        return [_draft(row, cited[row.id]) for row in rows]


def _mention(row: Any) -> SourceMention:
    return SourceMention(
        mention_id=row.id,
        source=MentionSource(row.source),
        text=row.text,
        url=row.url,
        sentiment=getattr(row, "sentiment", None),
    )


def _draft(row: Any, cited: list[SourceMention]) -> DraftRow:
    return DraftRow(
        draft_id=row.id,
        kind=DraftKind(row.kind),
        preset=DraftPreset(row.preset),
        text=row.text,
        reasoning_summary=row.reasoning_summary,
        prompt_version=row.prompt_version,
        created_at=row.created_at,
        cited=tuple(cited),
    )
