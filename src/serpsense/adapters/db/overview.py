"""What the pages show, read from Postgres (BUILD_PLAN §13). Every query reaches its rows through
`scoped`, the user's live brands, so a brand that isn't the user's, or is archived, reads like one
that doesn't exist. A brand page describes its latest scored scan, the one its card's scores come
from."""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import Connection

from serpsense.adapters.db.scoping import scoped
from serpsense.domain.enums import (
    CrisisLevel,
    ScanStatus,
)
from serpsense.ports.overview import (
    BrandCard,
)

CARDS = scoped(
    """
SELECT * FROM (
  SELECT b.id, b.name,
         ARRAY(SELECT o.name FROM brand_competitors c JOIN brands o ON o.id = c.brand_id
               WHERE c.competitor_brand_id = b.id AND o.id IN ({owned})
               ORDER BY o.name) AS competitor_of,
         v.health, v.crisis, v.crisis_level, s.created_at AS last_scan_at, s.status AS last_status
  FROM brands b
  LEFT JOIN LATERAL (SELECT created_at, status FROM scans WHERE brand_id = b.id
                     ORDER BY created_at DESC LIMIT 1) s ON true
  LEFT JOIN LATERAL (SELECT health, crisis, crisis_level FROM v_scan_scores
                     WHERE brand_id = b.id ORDER BY created_at DESC LIMIT 1) v ON true
  WHERE b.id IN ({owned}) AND (CAST(:brands AS uuid[]) IS NULL OR b.id = ANY(:brands))
) cards
ORDER BY cardinality(competitor_of) > 0, lower(name), id
"""
)


class SqlOverview:
    def __init__(self, connect: Callable[[], AbstractContextManager[Connection]]) -> None:
        self._connect = connect

    def brands(self, user_id: uuid.UUID) -> list[BrandCard]:
        with self._connect() as conn:
            return [_card(row) for row in conn.execute(CARDS, {"user": user_id, "brands": None})]


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
