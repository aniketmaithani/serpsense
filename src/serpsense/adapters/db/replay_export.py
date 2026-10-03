"""Record a brand's real scans as a replay recording (adapters/replay/recording.py), read-only.

Every scan of the brand that succeeded or is partial, oldest first, with the successful searches
it made, each with its own payload. A call served from the local cache kept none of its own, so
it gets the newest payload the same request had by then (the cache is shared, so from any call).
Labels are the active labelling prompt's, the newest per text. Nothing is written: the
transaction is read-only, so the command can safely point at the demo database.
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, text

from serpsense.adapters.replay.recording import (
    RecordedAnswer,
    RecordedLabel,
    RecordedScan,
    Recording,
    cleaned,
    dump,
    text_id,
)
from serpsense.domain.enums import LlmTask, MentionSource
from serpsense.domain.labelling import PROMPTS

BRAND = text("SELECT id FROM brands WHERE slug = :slug AND archived_at IS NULL")
SCANS = text(
    """
SELECT id, created_at FROM scans
WHERE brand_id = :brand AND status IN ('succeeded', 'partial') AND trigger <> 'replay'
ORDER BY created_at, id
"""
)
ANSWERS = text(
    """
SELECT c.scan_id, c.engine, c.params, c.params_hash, p.payload
FROM serp_calls c
JOIN LATERAL (
  SELECT r.payload FROM serp_calls k JOIN raw_responses r ON r.serp_call_id = k.id
  WHERE k.engine = c.engine AND k.params_hash = c.params_hash AND k.created_at <= c.created_at
  ORDER BY k.id = c.id DESC, k.created_at DESC, k.id DESC LIMIT 1
) p ON true
WHERE c.scan_id = ANY(:scans) AND c.outcome = 'succeeded'
ORDER BY c.created_at, c.id
"""
)
LABELS = text(
    """
SELECT m.source, coalesce(rv.text, m.text) AS text, e.prompt_version, e.is_about_brand,
       e.sentiment, e.topic, e.is_complaint, e.severity, e.reason
FROM enrichments e JOIN mentions m ON m.id = e.mention_id
LEFT JOIN mention_revisions rv ON rv.mention_id = e.mention_id AND rv.revision = e.revision
WHERE m.brand_id = :brand AND e.prompt_version = :prompt
ORDER BY e.created_at, e.id
"""
)
PROMPT = PROMPTS[LlmTask.LABEL_MENTIONS]  # the active labelling prompt's labels are recorded
LABEL_FIELDS = ("is_about_brand", "sentiment", "topic", "is_complaint", "severity", "reason")


class RecordingRefused(ValueError):
    """What was stored would make a recording holding something key-shaped."""


@dataclass(frozen=True)
class Exported:
    text: str  # the recording file's content
    scans: int
    answers: int
    labels: int


class SqlRecordingExport:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def export(self, slug: str) -> Exported:
        """Raises LookupError unless exactly one live brand has the slug, and RecordingRefused
        if what was stored would make a recording holding anything key-shaped."""
        with self._engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            brands = conn.execute(BRAND, {"slug": slug}).scalars().all()
            if len(brands) != 1:
                raise LookupError("exactly one live brand must have this slug")
            try:
                recording: Recording | None = _recording(conn, slug, brands[0])
            except ValidationError:  # its text quotes what it refused: never chained or shown
                recording = None
        if recording is None:
            raise RecordingRefused("the stored scans would make a recording that isn't safe")
        answers = sum(len(scan.answers) for scan in recording.scans)
        return Exported(dump(recording), len(recording.scans), answers, len(recording.labels))


def _recording(conn: Connection, slug: str, brand_id: uuid.UUID) -> Recording:
    scans = conn.execute(SCANS, {"brand": brand_id}).all()
    rows = conn.execute(ANSWERS, {"scans": [scan.id for scan in scans]}).all()
    payloads: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    recorded = []
    for scan in scans:
        answers: dict[str, RecordedAnswer] = {}
        for row in (r for r in rows if r.scan_id == scan.id):
            payload = cleaned(row.payload)
            index = payloads.setdefault(json.dumps(payload, sort_keys=True), len(kept))
            if index == len(kept):
                kept.append(payload)
            answer = RecordedAnswer(engine=row.engine, params=row.params, payload=index)
            answers.setdefault(row.params_hash, answer)  # a scan asks each request once
        recorded.append(RecordedScan(recorded_at=scan.created_at, answers=tuple(answers.values())))
    labels = _labels(conn.execute(LABELS, {"brand": brand_id, "prompt": PROMPT}).all())
    return Recording(
        format=1, brand=slug, payloads=tuple(kept), scans=tuple(recorded), labels=labels
    )


def _labels(rows: Sequence[Any]) -> tuple[RecordedLabel, ...]:
    """The newest label per text (rows come oldest first)."""
    labels: dict[str, RecordedLabel] = {}
    for row in rows:
        key = text_id(MentionSource(row.source), row.text)
        fields: Mapping[str, Any] = {name: getattr(row, name) for name in LABEL_FIELDS}
        labels[key] = RecordedLabel(text_id=key, prompt_version=row.prompt_version, **fields)
    return tuple(labels.values())
