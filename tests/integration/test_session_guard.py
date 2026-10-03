"""A signed-in visitor's session on real Postgres (ADR-0009): it slides, carries CSRF, and
closes on sign-out, here or everywhere."""

from datetime import timedelta

import pytest
from sqlalchemy import Engine, select

from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.services.auth import SignedIn
from serpsense.services.sessions import SessionGuard
from tests.integration.db_helpers import table
from tests.integration.test_sign_in import KEYS, NETWORK, Rig, rig

pytestmark = pytest.mark.integration

EVENTS, SESSIONS = table("audit_events"), table("sessions")


def guard(r: Rig) -> SessionGuard:
    return SessionGuard(lambda: SqlUnitOfWork(r.engine, r.jobs), r.clock, KEYS.csrf, session_days=7)


def signed_in(r: Rig) -> SignedIn:
    r.clock.at += timedelta(minutes=1)  # past the request limit's minute
    r.service.request_code(r.email, NETWORK)
    done = r.service.verify(r.email, r.code(), NETWORK)
    assert done is not None
    return done


def test_a_session_slides_hourly_and_carries_csrf(committing_engine: Engine) -> None:
    r = rig(committing_engine)
    first, sessions = signed_in(r), guard(r)
    start = r.clock.at
    expiry = select(SESSIONS.c.expires_at).where(SESSIONS.c.user_id == first.user_id)
    r.clock.at = start + timedelta(minutes=30)
    user = sessions.current(first.token)
    assert user is not None and user.user_id == first.user_id
    with committing_engine.connect() as conn:
        assert conn.execute(expiry).scalar_one() == start + timedelta(days=7)  # not yet an hour
    r.clock.at = start + timedelta(hours=2)
    assert sessions.current(first.token) is not None
    with committing_engine.connect() as conn:
        assert conn.execute(expiry).scalar_one() == start + timedelta(days=7, hours=2)
    assert sessions.csrf_valid(user, sessions.csrf_token(user))
    assert not sessions.csrf_valid(user, "forged") and sessions.current("") is None


def test_signing_out_closes_this_session_or_every_one(committing_engine: Engine) -> None:
    r = rig(committing_engine)
    sessions = guard(r)
    first = signed_in(r)
    user = sessions.current(first.token)
    assert user is not None
    sessions.log_out(user, NETWORK)
    assert sessions.current(first.token) is None
    second, third = signed_in(r), signed_in(r)  # two devices
    assert second.user_id == first.user_id  # the same account
    on_second = sessions.current(second.token)
    assert on_second is not None
    sessions.log_out_everywhere(on_second, NETWORK)
    assert sessions.current(second.token) is sessions.current(third.token) is None
    outs = select(EVENTS.c.action).where(EVENTS.c.actor_user_id == first.user_id)
    with committing_engine.connect() as conn:
        actions = set(conn.execute(outs).scalars())
    assert {"auth.logged_out", "auth.sessions_revoked"} <= actions
