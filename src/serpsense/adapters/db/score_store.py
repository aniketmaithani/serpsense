"""A scan's scores in Postgres: the inputs scoring reads, and the results (data-model §5 to §7).

A mention is first seen in the brand's earliest scan that observed it. Its label is that of its
latest revision, from its own labelling task: under the task's active prompt, or, until the
mention is labelled again, the newest under an earlier one, so a new prompt version doesn't
reset the brand's usual. A label that says the mention isn't about the brand leaves it out. A
surface was collected before a scan when an earlier scan's result for it is `succeeded`. A
brand's scans are ordered by when they were made, which differ: one is active at a time.
"""

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import CTE, Connection, Select, Table, func, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on, insert

from serpsense.adapters.db.models.llm import Enrichment
from serpsense.adapters.db.models.mentions import Mention, MentionRevision
from serpsense.adapters.db.models.observations import AppRatingObservation, MentionObservation
from serpsense.adapters.db.models.scans import Scan, ScanSurfaceResult
from serpsense.adapters.db.models.scores import CrisisComponentValue, ScoreRun, SurfaceScore
from serpsense.domain.enums import LlmTask, MentionSource, ScanStatus, Surface, SurfaceOutcome
from serpsense.domain.labelling import LABELLERS
from serpsense.domain.mention import SURFACE
from serpsense.domain.scoring.crisis import USUAL_SCANS
from serpsense.domain.scoring.scan import Earlier, Observed, ScanScores, ScoreInputs
from serpsense.domain.scoring.surfaces import NEWEST_REVIEWS

ENRICHMENTS = cast(Table, Enrichment.__table__)
MENTIONS = cast(Table, Mention.__table__)
REVISIONS = cast(Table, MentionRevision.__table__)
OBSERVATIONS = cast(Table, MentionObservation.__table__)
RATINGS = cast(Table, AppRatingObservation.__table__)
SCANS = cast(Table, Scan.__table__)
SURFACE_RESULTS = cast(Table, ScanSurfaceResult.__table__)
RUNS, SURFACE_SCORES = cast(Table, ScoreRun.__table__), cast(Table, SurfaceScore.__table__)
COMPONENTS = cast(Table, CrisisComponentValue.__table__)
OWN_TASK = [(source, task.value) for source, task in LABELLERS.items()]
UNDATED = datetime.min.replace(tzinfo=UTC)  # a review without a date sorts as the oldest


@dataclass(frozen=True)
class _Labelled:
    """A labelled mention about the brand, and when the brand's scans first saw it."""

    mention_id: uuid.UUID
    source: MentionSource
    sentiment: int
    severity: int
    published_at: datetime | None
    first_at: datetime


class SqlScoreStore:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def inputs(self, scan_id: uuid.UUID, *, prompts: Mapping[LlmTask, str]) -> ScoreInputs:
        brand_id, at = self._conn.execute(
            select(SCANS.c.brand_id, SCANS.c.created_at).where(SCANS.c.id == scan_id)
        ).one()
        earlier = self._earlier_scans(brand_id, at)
        labelled = self._labelled(brand_id, [scan_id, *(s for s, _ in earlier)], prompts)
        observed = self._observed(scan_id, at, labelled)
        firsts = self._first_collected(brand_id, at)
        ratings = select(RATINGS.c.rating_hundredths).where(RATINGS.c.scan_id == scan_id)
        return ScoreInputs(
            at=at,
            observed=observed,
            ratings=tuple(self._conn.execute(ratings).scalars()),
            newest_reviews=_newest_reviews(observed),
            earlier=_earlier([created for _, created in earlier], labelled, firsts),
            collected_before=_before(firsts, at),
        )

    def unscored(self) -> list[uuid.UUID]:
        scored = select(RUNS.c.scan_id).where(RUNS.c.scan_id == SCANS.c.id).exists()
        query = (
            select(SCANS.c.id)
            .where(SCANS.c.status.in_([ScanStatus.SUCCEEDED, ScanStatus.PARTIAL]), ~scored)
            .order_by(SCANS.c.created_at, SCANS.c.id)
        )
        return list(self._conn.execute(query).scalars())

    def record(self, scan_id: uuid.UUID, scores: ScanScores, *, version: str, at: datetime) -> bool:
        run = insert(RUNS).values(scan_id=scan_id, version=version, computed_at=at)
        added = self._conn.execute(run.on_conflict_do_nothing().returning(RUNS.c.scan_id))
        if added.first() is None:
            return False  # scored already: the first scores stay
        surfaces = scores.surfaces.items()
        self._insert(SURFACE_SCORES, [{"surface": s, "score": v} for s, v in surfaces], scan_id)
        components = scores.components.items()
        self._insert(COMPONENTS, [{"component": c, "value": v} for c, v in components], scan_id)
        return True

    def _insert(self, table: Table, rows: list[dict[str, object]], scan_id: uuid.UUID) -> None:
        if rows:  # a scan whose surfaces all showed nothing has no surface scores
            self._conn.execute(insert(table), [{"scan_id": scan_id, **row} for row in rows])

    def _earlier_scans(self, brand_id: uuid.UUID, at: datetime) -> list[tuple[uuid.UUID, datetime]]:
        query = (
            select(SCANS.c.id, SCANS.c.created_at)
            .join(RUNS, RUNS.c.scan_id == SCANS.c.id)
            .where(SCANS.c.brand_id == brand_id, SCANS.c.created_at < at)
            .order_by(SCANS.c.created_at.desc())
            .limit(USUAL_SCANS)
        )
        return [(row.id, row.created_at) for row in self._conn.execute(query)]

    def _labelled(
        self, brand_id: uuid.UUID, scans: Sequence[uuid.UUID], prompts: Mapping[LlmTask, str]
    ) -> list[_Labelled]:
        """The labelled mentions about the brand that these scans observed."""
        return [
            _Labelled(
                row.id,
                MentionSource(row.source),
                row.sentiment,
                row.severity,
                row.published_at,
                row.first_at,
            )
            for row in self._conn.execute(_labels(brand_id, scans, prompts))
        ]

    def _observed(
        self, scan_id: uuid.UUID, at: datetime, labelled: Sequence[_Labelled]
    ) -> tuple[Observed, ...]:
        labels = {label.mention_id: label for label in labelled}
        query = (
            select(OBSERVATIONS.c.mention_id, OBSERVATIONS.c.position)
            .where(OBSERVATIONS.c.scan_id == scan_id)
            .order_by(OBSERVATIONS.c.position.asc().nulls_last(), OBSERVATIONS.c.mention_id)
        )
        return tuple(
            Observed(
                label.source,
                label.sentiment,
                label.severity,
                row.position,
                label.published_at,
                new=label.first_at == at,
            )
            for row in self._conn.execute(query)
            if (label := labels.get(row.mention_id)) is not None
        )

    def _first_collected(self, brand_id: uuid.UUID, at: datetime) -> dict[Surface, datetime]:
        """When the brand's scans before `at` first collected each surface."""
        query = (
            select(SURFACE_RESULTS.c.surface, func.min(SCANS.c.created_at).label("first_at"))
            .join(SURFACE_RESULTS, SURFACE_RESULTS.c.scan_id == SCANS.c.id)
            .where(
                SCANS.c.brand_id == brand_id,
                SCANS.c.created_at < at,
                SURFACE_RESULTS.c.outcome == SurfaceOutcome.SUCCEEDED,
            )
            .group_by(SURFACE_RESULTS.c.surface)
        )
        return {Surface(row.surface): row.first_at for row in self._conn.execute(query)}


def _labels(
    brand_id: uuid.UUID, scans: Sequence[uuid.UUID], prompts: Mapping[LlmTask, str]
) -> Select[tuple[object, ...]]:
    """Each mention's label: its latest revision's, from its own task, the active prompt's first."""
    first = _first_seen(brand_id, scans)
    latest = (
        select(func.coalesce(func.max(REVISIONS.c.revision), 1))
        .where(REVISIONS.c.mention_id == MENTIONS.c.id)
        .scalar_subquery()
    )
    task = func.split_part(ENRICHMENTS.c.prompt_version, "/", 1)
    active = ENRICHMENTS.c.prompt_version.in_(list(prompts.values()))
    chosen = (
        select(
            MENTIONS.c.id,
            MENTIONS.c.source,
            MENTIONS.c.published_at,
            ENRICHMENTS.c.sentiment,
            ENRICHMENTS.c.severity,
            ENRICHMENTS.c.is_about_brand,
            first.c.first_at,
        )
        .join(first, first.c.mention_id == MENTIONS.c.id)
        .join(ENRICHMENTS, ENRICHMENTS.c.mention_id == MENTIONS.c.id)
        .where(ENRICHMENTS.c.revision == latest, tuple_(MENTIONS.c.source, task).in_(OWN_TASK))
        .order_by(MENTIONS.c.id, active.desc(), ENRICHMENTS.c.created_at.desc(), ENRICHMENTS.c.id)
        .ext(distinct_on(MENTIONS.c.id))
        .subquery()
    )
    return select(chosen).where(chosen.c.is_about_brand)


def _first_seen(brand_id: uuid.UUID, scans: Sequence[uuid.UUID]) -> CTE:
    """When the brand's scans first saw each mention these scans observed."""
    seen = select(OBSERVATIONS.c.mention_id).where(OBSERVATIONS.c.scan_id.in_(scans))
    return (
        select(OBSERVATIONS.c.mention_id, func.min(SCANS.c.created_at).label("first_at"))
        .join(SCANS, SCANS.c.id == OBSERVATIONS.c.scan_id)
        .where(SCANS.c.brand_id == brand_id, OBSERVATIONS.c.mention_id.in_(seen))
        .group_by(OBSERVATIONS.c.mention_id)
        .cte("first_seen")
    )


def _earlier(
    times: Sequence[datetime], labelled: Sequence[_Labelled], firsts: Mapping[Surface, datetime]
) -> tuple[Earlier, ...]:
    """Each earlier scan's negatives first seen in it, per surface, and what was collected
    before it."""
    negatives = Counter((m.first_at, SURFACE[m.source]) for m in labelled if m.sentiment < 0)
    return tuple(
        Earlier({s: n for (when, s), n in negatives.items() if when == at}, _before(firsts, at))
        for at in times
    )


def _before(firsts: Mapping[Surface, datetime], at: datetime) -> frozenset[Surface]:
    return frozenset(surface for surface, first in firsts.items() if first < at)


def _newest_reviews(observed: Sequence[Observed]) -> tuple[int, ...]:
    reviews = [o for o in observed if o.source is MentionSource.PLAY_REVIEW]
    reviews.sort(key=lambda o: o.published_at or UNDATED, reverse=True)  # stable on ties
    return tuple(o.sentiment for o in reviews[:NEWEST_REVIEWS])
