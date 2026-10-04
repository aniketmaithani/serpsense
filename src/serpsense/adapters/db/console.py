"""What the operator console shows, read from Postgres across every user (ADR-0014)."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta

from sqlalchemy import Connection, text

from serpsense.domain.enums import AccessDecision
from serpsense.ports.accounts import PSEUDONYM_DOMAIN
from serpsense.ports.console import BrandRow, RequestRow, Totals

MOST_BRANDS = MOST_REQUESTS = 200
DELETED = f"%@{PSEUDONYM_DOMAIN}"  # a deleted account's pseudonymised address (ADR-0013)
TOTALS = text(
    """
SELECT
  (SELECT count(*) FROM users WHERE deleted_at IS NULL) AS users,
  (SELECT count(*) FROM brands WHERE archived_at IS NULL) AS brands,
  (SELECT count(*) FROM brands WHERE archived_at IS NOT NULL) AS archived_brands,
  (SELECT count(*) FROM brands WHERE created_at >= :week_ago) AS brands_this_week,
  (SELECT count(*) FROM scans WHERE created_at >= :day_ago) AS scans_today,
  (SELECT count(*) FROM serp_calls
   WHERE served_from = 'live' AND created_at >= :month_began) AS searches_this_month,
  (SELECT count(*) FROM access_requests r
   WHERE NOT EXISTS (SELECT 1 FROM access_decisions d WHERE d.access_request_id = r.id)
     AND r.email NOT LIKE :deleted) AS pending_requests
"""
)
REQUESTS = text(
    """
SELECT r.id AS request_id, r.email, r.requested_at, d.decision, d.decided_at
FROM access_requests r
LEFT JOIN LATERAL (SELECT decision, decided_at FROM access_decisions
                   WHERE access_request_id = r.id ORDER BY decided_at DESC LIMIT 1) d ON true
WHERE r.email NOT LIKE :deleted
ORDER BY d.decided_at IS NOT NULL, d.decided_at DESC, r.requested_at, r.id
LIMIT :most
"""
)
BRANDS = text(
    """
SELECT b.name, u.email AS owner_email, b.created_at, b.archived_at IS NOT NULL AS archived,
       (SELECT max(s.created_at) FROM scans s WHERE s.brand_id = b.id) AS last_scan_at
FROM brands b JOIN users u ON u.id = b.owner_id
ORDER BY b.created_at DESC, b.id
LIMIT :most
"""
)


class SqlConsoleReads:
    def __init__(self, connect: Callable[[], AbstractContextManager[Connection]]) -> None:
        self._connect = connect

    def totals(self, at: datetime) -> Totals:
        month_began = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        since = {
            "week_ago": at - timedelta(days=7),
            "day_ago": at - timedelta(days=1),
            "month_began": month_began,
            "deleted": DELETED,
        }
        with self._connect() as conn:
            row = conn.execute(TOTALS, since).mappings().one()
        return Totals(**row)

    def requests(self) -> list[RequestRow]:
        with self._connect() as conn:
            rows = (
                conn.execute(REQUESTS, {"deleted": DELETED, "most": MOST_REQUESTS}).mappings().all()
            )
        return [RequestRow(**{**row, "decision": _decision(row["decision"])}) for row in rows]

    def brands(self) -> list[BrandRow]:
        with self._connect() as conn:
            rows = conn.execute(BRANDS, {"most": MOST_BRANDS}).mappings().all()
        return [BrandRow(**row) for row in rows]


def _decision(raw: str | None) -> AccessDecision | None:
    return None if raw is None else AccessDecision(raw)
