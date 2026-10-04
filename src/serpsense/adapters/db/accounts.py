"""User accounts in Postgres (data-model §1), and the account-deletion scrub (ADR-0013)."""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import (
    Column,
    ColumnElement,
    Connection,
    Delete,
    ScalarSelect,
    Table,
    Update,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from serpsense.adapters.db.models.access import AccessRequest
from serpsense.adapters.db.models.audit import AuditEvent, AuditEventNetwork
from serpsense.adapters.db.models.brands import Brand
from serpsense.adapters.db.models.identity import OtpCode, User, UserSession
from serpsense.adapters.db.models.outbox import OutboxMessage
from serpsense.domain.enums import AuditTarget
from serpsense.ports.accounts import Scrubbed, email_address

USERS, CODES = cast(Table, User.__table__), cast(Table, OtpCode.__table__)
SESSIONS, BRANDS = cast(Table, UserSession.__table__), cast(Table, Brand.__table__)
MESSAGES, REQUESTS = cast(Table, OutboxMessage.__table__), cast(Table, AccessRequest.__table__)
EVENTS, NETWORK = cast(Table, AuditEvent.__table__), cast(Table, AuditEventNetwork.__table__)
LIVE_USER = USERS.c.deleted_at.is_(None)
ZEROED = bytes(32)  # an HMAC of a 6-digit code could confirm a guessed address (ADR-0013)


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

    def email_of(self, user_id: uuid.UUID) -> str | None:
        query = select(USERS.c.email).where(USERS.c.id == user_id, LIVE_USER)
        return cast(str | None, self._conn.execute(query).scalar_one_or_none())

    def lock(self, user_id: uuid.UUID) -> str | None:
        query = select(USERS.c.email).where(USERS.c.id == user_id, LIVE_USER).with_for_update()
        return cast(str | None, self._conn.execute(query).scalar_one_or_none())

    def scrub(self, user_id: uuid.UUID, email: str, *, at: datetime) -> Scrubbed:
        # The address's codes find its pre-login events and code emails, so they go last.
        codes = select(CODES.c.id).where(CODES.c.email == email).scalar_subquery()
        network = self._delete_network(user_id, codes)
        messages = self._count(
            update(MESSAGES)
            .where(
                or_(
                    MESSAGES.c.user_id == user_id,
                    MESSAGES.c.otp_code_id.in_(codes),
                    MESSAGES.c.recipient_email == email,
                )
            )
            .values(recipient_email=_pseudonym(MESSAGES.c.id), sensitive_data_encrypted=None)
        )
        live = (CODES.c.consumed_at.is_(None)) & (CODES.c.superseded_at.is_(None))
        self._conn.execute(
            update(CODES).where(CODES.c.email == email, live).values(superseded_at=at)
        )
        scrubbed_codes = self._count(
            update(CODES)
            .where(CODES.c.email == email)
            .values(email=_pseudonym(CODES.c.id), request_ip=None, code_hash=ZEROED)
        )
        requests = self._count(
            update(REQUESTS)
            .where(REQUESTS.c.email == email)
            .values(email=_pseudonym(REQUESTS.c.id))
        )
        sessions = self._count(delete(SESSIONS).where(SESSIONS.c.user_id == user_id))
        brands = self._count(
            update(BRANDS)
            .where(BRANDS.c.owner_id == user_id)
            .values(archived_at=func.coalesce(BRANDS.c.archived_at, at), tone_notes=None)
        )
        self._conn.execute(
            update(USERS)
            .where(USERS.c.id == user_id)
            .values(email=_pseudonym(USERS.c.id), deleted_at=at)
        )
        return Scrubbed(brands, sessions, scrubbed_codes, messages, network, requests)

    def _delete_network(self, user_id: uuid.UUID, codes: ScalarSelect[uuid.UUID]) -> int:
        """Network details of the user's events, and of the pre-login events (no actor yet)
        about a code issued to their address."""
        theirs = select(EVENTS.c.id).where(
            or_(
                EVENTS.c.actor_user_id == user_id,
                (EVENTS.c.target_type == AuditTarget.USER) & (EVENTS.c.target_id == user_id),
                (EVENTS.c.target_type == AuditTarget.OTP_CODE) & EVENTS.c.target_id.in_(codes),
            )
        )
        return self._count(delete(NETWORK).where(NETWORK.c.audit_event_id.in_(theirs)))

    def _count(self, statement: Update | Delete) -> int:
        return self._conn.execute(statement).rowcount


def _pseudonym(row_id: Column[uuid.UUID]) -> ColumnElement[str]:
    """`ports.accounts.pseudonym`, computed per row in SQL."""
    return func.concat("deleted+", row_id, "@serpsense.invalid")
