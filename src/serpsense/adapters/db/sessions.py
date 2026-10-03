"""Sign-in sessions in Postgres (data-model §1)."""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, func, select, update
from sqlalchemy.dialects.postgresql import insert

from serpsense.adapters.db.models.identity import User, UserSession
from serpsense.domain.auth import MAX_SESSION_AGE, clean_user_agent
from serpsense.ports.sessions import ActiveSession, NewSession

SESSIONS = cast(Table, UserSession.__table__)
USERS = cast(Table, User.__table__)


class SqlSessions:
    """Works on the caller's connection and never commits: the unit of work does."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def create(self, new: NewSession) -> uuid.UUID:
        session_id = uuid.uuid4()
        row = {
            "id": session_id,
            "user_id": new.user_id,
            "token_hash": new.token_hash,
            "csrf_secret": new.csrf_secret,
            "created_at": new.at,
            "expires_at": new.expires_at,
            "ip": new.ip,
            "user_agent": clean_user_agent(new.user_agent),
        }
        self._conn.execute(insert(SESSIONS).values(row))
        return session_id

    def active(self, token_hash: bytes, *, at: datetime) -> ActiveSession | None:
        query = (
            select(SESSIONS.c.id, SESSIONS.c.user_id, SESSIONS.c.csrf_secret, SESSIONS.c.expires_at)
            .join(USERS, USERS.c.id == SESSIONS.c.user_id)
            .where(
                SESSIONS.c.token_hash == token_hash,
                SESSIONS.c.revoked_at.is_(None),
                SESSIONS.c.expires_at > at,
                SESSIONS.c.created_at > at - MAX_SESSION_AGE,
                USERS.c.deleted_at.is_(None),
            )
        )
        row = self._conn.execute(query).first()
        if row is None:
            return None
        return ActiveSession(row.id, row.user_id, row.csrf_secret, row.expires_at)

    def extend(self, session_id: uuid.UUID, *, at: datetime, expires_at: datetime) -> None:
        live = (SESSIONS.c.id == session_id) & SESSIONS.c.revoked_at.is_(None)
        live &= (SESSIONS.c.expires_at > at) & (SESSIONS.c.expires_at < expires_at)
        capped = func.least(expires_at, SESSIONS.c.created_at + MAX_SESSION_AGE)
        self._conn.execute(update(SESSIONS).where(live).values(expires_at=capped))

    def revoke(self, session_id: uuid.UUID, *, at: datetime) -> None:
        live = (SESSIONS.c.id == session_id) & SESSIONS.c.revoked_at.is_(None)
        self._conn.execute(update(SESSIONS).where(live).values(revoked_at=at))

    def revoke_all(self, user_id: uuid.UUID, *, at: datetime) -> int:
        live = (SESSIONS.c.user_id == user_id) & SESSIONS.c.revoked_at.is_(None)
        revoked = self._conn.execute(
            update(SESSIONS).where(live).values(revoked_at=at).returning(SESSIONS.c.id)
        )
        return len(revoked.all())
