"""What the pages show, read from Postgres (BUILD_PLAN §13). Every query reaches its rows through
`scoped`, the user's live brands, so a brand that isn't the user's, or is archived, reads like one
that doesn't exist. A brand page describes its latest scored scan, the one its card's scores come
from (both from one row of `v_scan_scores`), with each mention's latest revision and the label
scoring would use: the active prompt's, else the newest (docs/scoring.md)."""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import Connection

from serpsense.adapters.db.scoping import scoped
from serpsense.domain.enums import (
    AlertRule,
    CrisisComponent,
    CrisisLevel,
    MentionSource,
    ScanStatus,
    Surface,
    SurfaceOutcome,
)
from serpsense.domain.labelling import PROMPTS
from serpsense.domain.scoring.tuning import DEFAULT_TUNING, CrisisTuning
from serpsense.ports.overview import (
    AlertRow,
    BrandCard,
    BrandPage,
    MentionRow,
    SurfaceRow,
    TrendPoint,
)

TREND_SCANS, PAGE_MENTIONS, PAGE_ALERTS = 60, 10, 10
ACTIVE_PROMPTS = sorted(PROMPTS.values())
CARDS = scoped(
    """
SELECT * FROM (
  SELECT b.id, b.name,
         ARRAY(SELECT o.name FROM brand_competitors c JOIN brands o ON o.id = c.brand_id
               WHERE c.competitor_brand_id = b.id AND o.id IN ({owned})
               ORDER BY o.name) AS competitor_of,
         v.scan_id AS scored_scan_id, v.health, v.crisis, v.crisis_level,
         s.created_at AS last_scan_at, s.status AS last_status
  FROM brands b
  LEFT JOIN LATERAL (SELECT created_at, status FROM scans WHERE brand_id = b.id
                     ORDER BY created_at DESC, id DESC LIMIT 1) s ON true
  LEFT JOIN LATERAL (SELECT scan_id, health, crisis, crisis_level FROM v_scan_scores
                     WHERE brand_id = b.id ORDER BY created_at DESC, scan_id DESC LIMIT 1) v
         ON true
  WHERE b.id IN ({owned}) AND (CAST(:brands AS uuid[]) IS NULL OR b.id = ANY(:brands))
) cards
ORDER BY cardinality(competitor_of) > 0, lower(name), id
"""
)
TREND = scoped(
    """
SELECT * FROM (SELECT created_at, scan_id, health, crisis, crisis_level FROM v_scan_scores
               WHERE brand_id IN ({owned} AND id = :brand)
               ORDER BY created_at DESC, scan_id DESC LIMIT :limit) t
ORDER BY created_at, scan_id
"""
)
SURFACES = scoped(
    """
SELECT r.surface, r.outcome, ss.score
FROM scan_surface_results r JOIN scans s ON s.id = r.scan_id
LEFT JOIN surface_scores ss ON ss.scan_id = r.scan_id AND ss.surface = r.surface
WHERE r.scan_id = :scan AND s.brand_id IN ({owned} AND id = :brand)
ORDER BY r.surface
"""
)
MENTIONS = scoped(
    """
SELECT m.source, coalesce(rv.text, m.text) AS text, m.url, m.outlet, o.position, m.published_at,
       e.sentiment, e.reason, na.narrative_id AS story_id, count(*) OVER () AS total
FROM mention_observations o JOIN mentions m ON m.id = o.mention_id
LEFT JOIN LATERAL (SELECT narrative_id FROM narrative_assignments WHERE mention_id = m.id
                   ORDER BY created_at DESC, id DESC LIMIT 1) na ON true
LEFT JOIN LATERAL (SELECT revision, text FROM mention_revisions WHERE mention_id = m.id
                   ORDER BY revision DESC LIMIT 1) rv ON true
LEFT JOIN LATERAL (SELECT sentiment, severity, reason, is_about_brand FROM enrichments
                   WHERE mention_id = m.id AND revision = coalesce(rv.revision, 1)
                   ORDER BY prompt_version = ANY(:active) DESC, created_at DESC, id DESC
                   LIMIT 1) e ON true
WHERE o.scan_id = :scan AND e.is_about_brand IS NOT FALSE
  AND m.brand_id IN ({owned} AND id = :brand)
ORDER BY e.sentiment ASC NULLS LAST, e.severity DESC NULLS LAST, o.position ASC NULLS LAST,
         m.published_at DESC NULLS LAST, m.id
LIMIT :limit OFFSET :offset
"""
)
ALERTS = scoped(
    """
SELECT a.rule, a.created_at, coalesce(n.title, 'Alert') AS title, e.text AS explanation
FROM alerts a JOIN scans s ON s.id = a.scan_id
LEFT JOIN notifications n ON n.alert_id = a.id AND n.user_id = :user
LEFT JOIN alert_explanations e ON e.alert_id = a.id
WHERE s.brand_id IN ({owned} AND id = :brand)
ORDER BY a.created_at DESC, a.id LIMIT :limit
"""
)
SIGNALS = scoped(
    """
SELECT x.component, x.value FROM crisis_components x JOIN scans s ON s.id = x.scan_id
WHERE x.scan_id = :scan AND s.brand_id IN ({owned} AND id = :brand)
"""
)
SCORED = scoped(
    """
SELECT count(*) FROM score_runs r JOIN scans s ON s.id = r.scan_id
WHERE s.brand_id IN ({owned} AND id = :brand)
"""
)
TUNING = scoped(
    """
SELECT warm_up_scans, medium_at, high_at, cooldown_hours, spread_mentions, spread_surfaces
FROM brand_crisis_tuning_versions WHERE brand_id IN ({owned} AND id = :brand)
ORDER BY created_at DESC LIMIT 1
"""
)
COMPETITORS = scoped(
    """
SELECT competitor_brand_id FROM brand_competitors WHERE brand_id IN ({owned} AND id = :brand)
"""
)


class SqlOverview:
    def __init__(self, connect: Callable[[], AbstractContextManager[Connection]]) -> None:
        self._connect = connect

    def brands(self, user_id: uuid.UUID) -> list[BrandCard]:
        with self._connect() as conn:
            return [_card(row) for row in conn.execute(CARDS, {"user": user_id, "brands": None})]

    def brand(
        self, user_id: uuid.UUID, brand_id: uuid.UUID, *, mention_page: int = 1
    ) -> BrandPage | None:
        known = {"user": user_id, "brand": brand_id}
        with self._connect() as conn:
            row = conn.execute(CARDS, {"user": user_id, "brands": [brand_id]}).first()
            if row is None:
                return None
            in_scan = known | {"scan": row.scored_scan_id}
            mentions, page, pages = _mentions(conn, in_scan, max(1, mention_page))
            tuning = conn.execute(TUNING, known).first()
            return BrandPage(
                card=_card(row),
                trend=tuple(map(_point, conn.execute(TREND, known | {"limit": TREND_SCANS}))),
                surfaces=tuple(map(_surface, conn.execute(SURFACES, in_scan))),
                mentions=mentions,
                alerts=tuple(map(_alert, conn.execute(ALERTS, known | {"limit": PAGE_ALERTS}))),
                competitors=_competitors(conn, known),
                components={CrisisComponent(c): v for c, v in conn.execute(SIGNALS, in_scan)},
                scored_scans=conn.execute(SCORED, known).scalar_one(),
                tuning=DEFAULT_TUNING if tuning is None else CrisisTuning(**tuning._asdict()),
                mention_page=page,
                mention_pages=pages,
            )


def _mentions(
    conn: Connection, in_scan: dict[str, Any], wanted: int
) -> tuple[tuple[MentionRow, ...], int, int]:
    """One page of the scan's mentions, the page it is, and how many pages there are; a page
    past the last reads the last."""
    values = in_scan | {"active": ACTIVE_PROMPTS, "limit": PAGE_MENTIONS}

    def page(number: int) -> list[Any]:
        return list(conn.execute(MENTIONS, values | {"offset": (number - 1) * PAGE_MENTIONS}))

    rows = page(wanted)
    if not rows and wanted > 1:
        first = page(1)
        wanted = _pages(first[0].total) if first else 1
        rows = page(wanted) if wanted > 1 else first
    return tuple(map(_mention, rows)), wanted, _pages(rows[0].total if rows else 0)


def _pages(total: int) -> int:
    return max(1, -(-total // PAGE_MENTIONS))


def _competitors(conn: Connection, known: dict[str, uuid.UUID]) -> tuple[BrandCard, ...]:
    rivals = list(conn.execute(COMPETITORS, known).scalars())
    if not rivals:
        return ()
    return tuple(map(_card, conn.execute(CARDS, {"user": known["user"], "brands": rivals})))


def _level(value: str | None) -> CrisisLevel | None:
    return None if value is None else CrisisLevel(value)


def _card(row: Any) -> BrandCard:
    status = None if row.last_status is None else ScanStatus(row.last_status)
    return BrandCard(
        brand_id=row.id,
        name=row.name,
        competitor_of=tuple(row.competitor_of),
        health=row.health,
        crisis=row.crisis,
        level=_level(row.crisis_level),
        last_scan_at=row.last_scan_at,
        last_status=status,
    )


def _point(row: Any) -> TrendPoint:
    return TrendPoint(row.created_at, row.health, row.crisis, _level(row.crisis_level))


def _surface(row: Any) -> SurfaceRow:
    return SurfaceRow(Surface(row.surface), SurfaceOutcome(row.outcome), row.score)


def _mention(row: Any) -> MentionRow:
    return MentionRow(
        source=MentionSource(row.source),
        text=row.text,
        url=row.url,
        outlet=row.outlet,
        position=row.position,
        published_at=row.published_at,
        sentiment=row.sentiment,
        reason=row.reason,
        story_id=row.story_id,
    )


def _alert(row: Any) -> AlertRow:
    return AlertRow(AlertRule(row.rule), row.created_at, row.title, row.explanation)
