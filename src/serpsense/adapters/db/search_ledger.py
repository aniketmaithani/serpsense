"""The SerpApi ledger in Postgres: `serp_calls` and `raw_responses` (data-model §5)."""

import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Connection, Table, and_, func, insert, or_, select

from serpsense.adapters.db.models.identity import UserSearchBudget
from serpsense.adapters.db.models.search import RawResponse, SerpCall
from serpsense.domain.enums import SerpCallOutcome, SerpEngine, SerpErrorCode, ServedFrom
from serpsense.observability import get_logger
from serpsense.ports.search_ledger import CallRecord
from serpsense.ports.search_provider import SearchRequest

log = get_logger(__name__)

# Opens a transaction and commits it on exit (`engine.begin` in production).
Transaction = Callable[[], AbstractContextManager[Connection]]

CALLS = cast(Table, SerpCall.__table__)
RAW = cast(Table, RawResponse.__table__)
BUDGETS = cast(Table, UserSearchBudget.__table__)
# Attempts that reached SerpApi: every failure, and successes not served from the local cache.
REACHED_SERPAPI = or_(
    CALLS.c.outcome == SerpCallOutcome.FAILED,
    and_(
        CALLS.c.outcome == SerpCallOutcome.SUCCEEDED, CALLS.c.served_from != ServedFrom.LOCAL_CACHE
    ),
)


class SqlSearchLedger:
    """Each method runs in a transaction of its own, so a recorded call survives whatever the
    caller does next."""

    def __init__(self, transaction: Transaction) -> None:
        self._transaction = transaction

    def record_call(self, call: CallRecord) -> uuid.UUID:
        call_id = uuid.uuid4()
        row = {
            "id": call_id,
            "user_id": call.user_id,
            "scan_id": call.scan_id,
            "engine": call.request.engine,
            "params_hash": call.request.params_hash,
            "params": dict(call.request.params),
            "served_from": call.served_from,
            "outcome": call.outcome,
            "http_status": call.http_status,
            "error_code": call.error_code.value if call.error_code else None,
            "latency_ms": call.latency_ms,
            "created_at": call.created_at,
        }
        with self._transaction() as conn:
            conn.execute(insert(CALLS).values(row))
        return call_id

    def store_payload(
        self, call_id: uuid.UUID, payload: Mapping[str, Any], *, at: datetime
    ) -> None:
        row = {
            "id": uuid.uuid4(),
            "serp_call_id": call_id,
            "payload": dict(payload),
            "created_at": at,
        }
        with self._transaction() as conn:
            conn.execute(insert(RAW).values(row))

    def recent_attempts(
        self, engine: SerpEngine, *, since: datetime, limit: int
    ) -> list[SerpErrorCode | None]:
        query = (
            select(CALLS.c.error_code)
            .where(CALLS.c.engine == engine, CALLS.c.created_at >= since, REACHED_SERPAPI)
            .order_by(CALLS.c.created_at.desc(), CALLS.c.id.desc())
            .limit(limit)
        )
        with self._transaction() as conn:
            codes = conn.execute(query).scalars().all()
        return [_error_code(code) for code in codes]

    def live_calls(
        self, *, since: datetime, user_id: uuid.UUID | None = None, scan_id: uuid.UUID | None = None
    ) -> int:
        conditions = [CALLS.c.served_from == ServedFrom.LIVE, CALLS.c.created_at >= since]
        if user_id is not None:
            conditions.append(CALLS.c.user_id == user_id)
        if scan_id is not None:
            conditions.append(CALLS.c.scan_id == scan_id)
        with self._transaction() as conn:
            return conn.execute(
                select(func.count()).select_from(CALLS).where(*conditions)
            ).scalar_one()

    def times_answered(self, request: SearchRequest) -> int:
        """How many successful calls this request has had: replay mode's place in its
        recording (adapters/serp/replay.py), kept in Postgres like every other fact."""
        query = (
            select(func.count())
            .select_from(CALLS)
            .where(
                CALLS.c.engine == request.engine,
                CALLS.c.params_hash == request.params_hash,
                CALLS.c.outcome == SerpCallOutcome.SUCCEEDED,
            )
        )
        with self._transaction() as conn:
            answered: int = conn.execute(query).scalar_one()
        return answered

    def monthly_budget(self, user_id: uuid.UUID, *, at: datetime) -> int | None:
        query = (
            select(BUDGETS.c.monthly_searches)
            .where(BUDGETS.c.user_id == user_id, BUDGETS.c.effective_from <= at)
            .order_by(BUDGETS.c.effective_from.desc())
            .limit(1)
        )
        with self._transaction() as conn:
            budget: int | None = conn.execute(query).scalar_one_or_none()
        return budget


def _error_code(value: str | None) -> SerpErrorCode | None:
    """None for a success; a code the domain doesn't know is a permanent failure."""
    if value is None:
        return None
    try:
        return SerpErrorCode(value)
    except ValueError:
        log.warning("serp_call.unknown_error_code_read", error_code=value)
        return SerpErrorCode.SEARCH_ERROR
