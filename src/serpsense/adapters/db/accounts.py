"""User accounts in Postgres (data-model §1)."""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import Connection, Table, select
from sqlalchemy.dialects.postgresql import insert

from serpsense.adapters.db.models.identity import User
from serpsense.ports.accounts import email_address

USERS = cast(Table, User.__table__)


class SqlAccounts:
    """Works on the caller's connection and never commits."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def user_for(self, email: str, *, at: datetime) -> uuid.UUID:
        email = email_address(email)
        new = uuid.uuid4()
        values = {"id": new, "email": email, "created_at": at}
        statement = insert(USERS).values(**values).on_conflict_do_nothing(index_elements=["email"])
        if self._conn.execute(statement.returning(USERS.c.id)).first() is not None:
            return new
        query = select(USERS.c.id).where(USERS.c.email == email)  # citext: ignores case
        return cast(uuid.UUID, self._conn.execute(query).scalar_one())
