"""The LLM call ledger in Postgres: `llm_calls` (data-model §6)."""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, func, insert, select

from serpsense.adapters.db.models.llm import LlmCall
from serpsense.ports.llm_ledger import LlmCallRecord

# Opens a transaction and commits it on exit (`engine.begin` in production).
Transaction = Callable[[], AbstractContextManager[Connection]]

CALLS = cast(Table, LlmCall.__table__)


class SqlLlmLedger:
    """Each method runs in a transaction of its own, so a recorded call survives whatever the
    caller does next (the one deliberate exception to working inside the unit of work)."""

    def __init__(self, transaction: Transaction) -> None:
        self._transaction = transaction

    def record(self, call: LlmCallRecord) -> uuid.UUID:
        call_id = uuid.uuid4()
        row = {
            "id": call_id,
            "user_id": call.user_id,
            "scan_id": call.scan_id,
            "task": call.task,
            "requested_model": call.requested_model,
            "served_model": call.served_model,
            "prompt_version": call.prompt_version,
            "request_settings": dict(call.request_settings),
            "input_tokens": call.usage.input,
            "output_tokens": call.usage.output,
            "cache_read_tokens": call.usage.cache_read,
            "cache_write_tokens": call.usage.cache_write,
            "cost_micros": call.cost_micros,
            "currency": call.currency,
            "stop_reason": call.stop_reason,
            "outcome": call.outcome,
            "latency_ms": call.latency_ms,
            "created_at": call.created_at,
        }
        with self._transaction() as conn:
            conn.execute(insert(CALLS).values(row))
        return call_id

    def spent_since(self, user_id: uuid.UUID, since: datetime) -> int:
        query = select(func.coalesce(func.sum(CALLS.c.cost_micros), 0)).where(
            CALLS.c.user_id == user_id, CALLS.c.created_at >= since
        )
        with self._transaction() as conn:
            return int(conn.execute(query).scalar_one())
