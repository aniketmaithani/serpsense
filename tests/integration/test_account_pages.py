"""Settings → Account over HTTP, on real Postgres (ADR-0009, ADR-0013): signing out everywhere
and deleting the account, each behind the session's CSRF token."""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from tests.fakes import FixedClock
from tests.integration.db_helpers import NOW, table
from tests.integration.test_login_pages import browser, code_for, token

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

ACCOUNT, REFUSED = "/settings/account", "That code or address didn"


def signed_in(engine: Engine, clock: FixedClock, email: str) -> TestClient:
    client = browser(engine, clock)
    form = token(client.get("/login").text)
    client.post("/login", data={"form_token": form, "email": email})
    code = code_for(engine, email)
    verified = client.post("/verify", data={"form_token": form, "email": email, "code": code})
    assert verified.status_code == 303 and client.get("/").status_code == 200
    return client


def test_deleting_the_account_through_the_pages(committing_engine: Engine) -> None:
    clock, email = FixedClock(NOW), f"{uuid.uuid4().hex[:10]}@example.com"
    client = signed_in(committing_engine, clock, email)
    page = client.get(ACCOUNT)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    csrf = token(page.text)
    assert client.post(f"{ACCOUNT}/delete/code", data={"csrf_token": "forged"}).status_code == 403
    clock.at = NOW + timedelta(minutes=2)  # a code a minute at most, per address
    asked = client.post(f"{ACCOUNT}/delete/code", data={"csrf_token": csrf})
    assert asked.status_code == 200 and 'action="/settings/account/delete"' in asked.text
    code = code_for(committing_engine, email)
    attempts = ({"code": code, "email": "someone@example.com"}, {"code": "abcdef", "email": email})
    for wrong in attempts:  # a wrong address and a wrong code look the same
        refused = client.post(f"{ACCOUNT}/delete", data={"csrf_token": csrf, **wrong})
        assert refused.status_code == 400 and REFUSED in refused.text
    forged = {"csrf_token": "forged", "code": code, "email": email}
    assert client.post(f"{ACCOUNT}/delete", data=forged).status_code == 403
    session = client.cookies["serpsense_session"]
    deleted = client.post(
        f"{ACCOUNT}/delete", data={"csrf_token": csrf, "code": code, "email": email}
    )
    assert deleted.status_code == 303 and deleted.headers["location"] == "/login?deleted=1"
    assert 'serpsense_session=""' in deleted.headers["set-cookie"]
    assert "Your account was deleted." in client.get("/login?deleted=1").text
    client.cookies.set("serpsense_session", session)
    assert client.get(ACCOUNT).headers["location"] == "/login"  # that session is gone too


def test_signed_out_requests_change_nothing(committing_engine: Engine) -> None:
    client = browser(committing_engine)
    assert client.get(ACCOUNT).headers["location"] == "/login"
    for path in ("/delete/code", "/delete", "/sign-out-everywhere"):
        response = client.post(f"{ACCOUNT}{path}", data={"csrf_token": "x"})
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_signing_out_everywhere_ends_every_session(committing_engine: Engine) -> None:
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    clock = FixedClock(NOW)
    here = signed_in(committing_engine, clock, email)
    clock.at = NOW + timedelta(minutes=2)
    there = signed_in(committing_engine, clock, email)
    csrf = token(here.get(ACCOUNT).text)
    assert (
        here.post(f"{ACCOUNT}/sign-out-everywhere", data={"csrf_token": "forged"}).status_code
        == 403
    )
    out = here.post(f"{ACCOUNT}/sign-out-everywhere", data={"csrf_token": csrf})
    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert there.get("/").headers["location"] == "/login"


def test_a_deletion_held_up_by_an_email_being_sent_says_try_again(
    committing_engine: Engine, impatient_engine: Engine
) -> None:
    clock, email = FixedClock(NOW), f"{uuid.uuid4().hex[:10]}@example.com"
    client = signed_in(impatient_engine, clock, email)
    csrf = token(client.get(ACCOUNT).text)
    clock.at = NOW + timedelta(minutes=2)
    client.post(f"{ACCOUNT}/delete/code", data={"csrf_token": csrf})
    form = {"csrf_token": csrf, "code": code_for(committing_engine, email), "email": email}
    messages = table("outbox_messages")
    pending = select(messages.c.id).where(
        messages.c.recipient_email == email, messages.c.status == "pending"
    )
    with committing_engine.connect() as sender, sender.begin():
        assert sender.execute(pending.with_for_update()).all()  # a dispatcher sending them
        busy = client.post(f"{ACCOUNT}/delete", data=form)
    assert busy.status_code == 503 and busy.headers["retry-after"] == "60"
    assert "try again in a minute" in busy.text
    assert client.post(f"{ACCOUNT}/delete", data=form).status_code == 303  # the same code
