"""Leases as Postgres advisory locks (ADR-0003): a session-level lock on a connection of its own,
outside any transaction, so it lasts exactly as long as the block (or the process) and needs no
table. The connection is held idle meanwhile, which suits work a person waits for, one at a time
each."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

TRY_LOCK = text("SELECT pg_try_advisory_lock(hashtextextended(:name, 0))")
UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended(:name, 0))")


class SqlLeases:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def hold(self, name: str) -> Iterator[bool]:
        with self._engine.connect() as conn:
            session = conn.execution_options(isolation_level="AUTOCOMMIT")
            held = bool(session.execute(TRY_LOCK, {"name": name}).scalar_one())
            try:
                yield held
            finally:
                if held:
                    session.execute(UNLOCK, {"name": name})
