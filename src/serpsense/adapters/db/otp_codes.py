"""Sign-in codes in Postgres (data-model §1): `uq_otp_codes_one_live_per_email` keeps one live
(neither consumed nor superseded) code per email, and attempts are append-only rows."""

import uuid
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import cast

from sqlalchemy import Connection, Table, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from serpsense.adapters.db.models.identity import OtpCode, OtpVerifyAttempt
from serpsense.ports.otp_codes import LiveCode

CODES = cast(Table, OtpCode.__table__)
ATTEMPTS = cast(Table, OtpVerifyAttempt.__table__)
LIVE = CODES.c.consumed_at.is_(None) & CODES.c.superseded_at.is_(None)
ONE_LIVE = "uq_otp_codes_one_live_per_email"
REQUESTS_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(lower(:email), 0))")


class SqlOtpCodes:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def lock_requests(self, email: str) -> None:
        self._conn.execute(REQUESTS_LOCK, {"email": email})

    def issued_since(self, email: str, since: datetime) -> list[datetime]:
        query = (
            select(CODES.c.created_at)
            .where(CODES.c.email == email, CODES.c.created_at >= since)
            .order_by(CODES.c.created_at)
        )
        return list(self._conn.execute(query).scalars())

    def issued_in_all_since(self, since: datetime) -> int:
        query = select(func.count()).where(CODES.c.created_at >= since)
        return int(self._conn.execute(query).scalar_one())

    def issue(
        self,
        email: str,
        code_hash: bytes,
        *,
        at: datetime,
        expires_at: datetime,
        ip: IPv4Address | IPv6Address | None,
    ) -> uuid.UUID | None:
        code_id = uuid.uuid4()
        row = {"id": code_id, "email": email, "code_hash": code_hash, "request_ip": ip}
        try:
            with self._conn.begin_nested():  # a clash rolls back only this, not the caller's work
                live = update(CODES).where(CODES.c.email == email, LIVE)
                self._conn.execute(live.values(superseded_at=at))
                self._conn.execute(
                    insert(CODES).values(created_at=at, expires_at=expires_at, **row)
                )
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != ONE_LIVE:
                raise
            return None  # another request's code went live first
        return code_id

    def lock_live(self, email: str) -> LiveCode | None:
        # Lock first, then count in a statement of its own: a count taken with the lock would
        # come from before the wait and miss the guesses that held the lock meanwhile.
        locked = select(CODES.c.id, CODES.c.code_hash, CODES.c.expires_at).where(
            CODES.c.email == email, LIVE
        )
        row = self._conn.execute(locked.with_for_update()).first()
        if row is None:
            return None
        failed = select(func.count()).where(ATTEMPTS.c.otp_code_id == row.id, ~ATTEMPTS.c.succeeded)
        return LiveCode(
            row.id, row.code_hash, row.expires_at, self._conn.execute(failed).scalar_one()
        )

    def attempt(self, code_id: uuid.UUID, *, succeeded: bool, at: datetime) -> None:
        values = {"id": uuid.uuid4(), "otp_code_id": code_id, "succeeded": succeeded}
        self._conn.execute(insert(ATTEMPTS).values(attempted_at=at, **values))

    def consume(self, code_id: uuid.UUID, *, at: datetime) -> None:
        self._conn.execute(update(CODES).where(CODES.c.id == code_id, LIVE).values(consumed_at=at))
