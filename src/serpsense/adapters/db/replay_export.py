"""Record a brand's real scans as a replay recording (adapters/replay/recording.py), read-only.

Every scan of the brand that succeeded or is partial, oldest first, with its settings and the
successful searches it made, each with its own payload, kept only as far as its parser reads it.
A call served from the local cache kept none of its own, so it gets the newest payload the same
request had by then (the cache is shared, so from any call). Labels are the latest per text,
the active labelling prompt's. Nothing replayed is recorded again: replay scans are left out, and
so is any output made by the model `replay`. The transaction is read-only, so the command can
safely point at the demo database.
"""

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
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
from serpsense.domain.enums import LlmTask, MentionSource, SerpEngine
from serpsense.domain.labelling import PROMPTS

REPLAYED = "replay"  # the model replay mode answers as (adapters/llm/replay.py)
BRAND = text("SELECT id, name FROM brands WHERE slug = :slug AND archived_at IS NULL")
SCANS = text(
    """
SELECT id, created_at, settings_snapshot FROM scans
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
NOT_REPLAYED = "llm_call_id IN (SELECT id FROM llm_calls WHERE served_model <> :replayed)"
LABELS = text(
    f"""
SELECT m.source, coalesce(rv.text, m.text) AS text, e.prompt_version, e.is_about_brand,
       e.sentiment, e.topic, e.is_complaint, e.severity, e.reason
FROM enrichments e JOIN mentions m ON m.id = e.mention_id
LEFT JOIN mention_revisions rv ON rv.mention_id = e.mention_id AND rv.revision = e.revision
WHERE m.brand_id = :brand AND e.prompt_version = :prompt AND e.{NOT_REPLAYED}
ORDER BY e.created_at, e.id
"""  # noqa: S608 (constant fragments only; every value is a bound parameter)
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
        with read_only(self._engine) as conn:
            brands = conn.execute(BRAND, {"slug": slug}).all()
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


@contextmanager
def read_only(engine: Engine) -> Iterator[Connection]:
    """A connection whose transaction refuses every write, rolled back when done."""
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield conn
        finally:
            conn.rollback()


def _recording(conn: Connection, slug: str, brand: Any) -> Recording:
    scans = conn.execute(SCANS, {"brand": brand.id}).all()
    rows = conn.execute(ANSWERS, {"scans": [scan.id for scan in scans]}).all()
    payloads: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    recorded = []
    for scan in scans:
        answers: dict[str, RecordedAnswer] = {}
        for row in (r for r in rows if r.scan_id == scan.id):
            payload = cleaned(SerpEngine(row.engine), row.payload)
            index = payloads.setdefault(json.dumps(payload, sort_keys=True), len(kept))
            if index == len(kept):
                kept.append(payload)
            answer = RecordedAnswer(engine=row.engine, params=row.params, payload=index)
            answers.setdefault(row.params_hash, answer)  # a scan asks each request once
        settings, found = scan.settings_snapshot, tuple(answers.values())
        recorded.append(RecordedScan(recorded_at=scan.created_at, settings=settings, answers=found))
    params = {"brand": brand.id, "replayed": REPLAYED}
    return Recording(
        format=1,
        brand=slug,
        name=brand.name,
        payloads=tuple(kept),
        scans=tuple(recorded),
        labels=_labels(conn.execute(LABELS, params | {"prompt": PROMPT}).all()),
    )


def _labels(rows: Sequence[Any]) -> tuple[RecordedLabel, ...]:
    """The newest label per text (rows come oldest first)."""
    labels: dict[str, RecordedLabel] = {}
    for row in rows:
        key = text_id(MentionSource(row.source), row.text)
        fields: Mapping[str, Any] = {name: getattr(row, name) for name in LABEL_FIELDS}
        labels[key] = RecordedLabel(text_id=key, prompt_version=row.prompt_version, **fields)
    return tuple(labels.values())
