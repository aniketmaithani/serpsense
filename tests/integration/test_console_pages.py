"""The operator console over HTTP, on real Postgres (ADR-0014): off without a password; a password
login, a signed cookie and CSRF on every write; approving an access request lets its address in."""

import dataclasses
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from serpsense.adapters.db.access import REQUESTS, SqlAccessRequests
from serpsense.adapters.db.console import SqlConsoleReads
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.domain.console import console_keys
from serpsense.domain.enums import SignupMode
from serpsense.services.console import Console, ConsoleGate, GatePorts
from tests.fakes import FixedClock
from tests.integration.db_helpers import NOW
from tests.integration.test_login_pages import browser, token
from tests.integration.test_sign_in import Jobs, Limiter

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

PASSWORD = "a long console password"
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def console_client(engine: Engine) -> TestClient:
    client = browser(engine)
    ports = GatePorts(lambda: SqlUnitOfWork(engine, Jobs()), Limiter(), FixedClock(NOW))
    gate = ConsoleGate(ports, console_keys(b"k" * 32, PASSWORD))
    container = client.app.state.container  # type: ignore[attr-defined]
    reads = SqlConsoleReads(engine.connect)
    console = Console(gate, reads, ports.unit_of_work, ports.clock, default_mode=SignupMode.INVITE)
    client.app.state.container = dataclasses.replace(container, console=console)  # type: ignore[attr-defined]
    return client


def asked(engine: Engine) -> tuple[str, uuid.UUID]:
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    with engine.begin() as conn:
        SqlAccessRequests(conn).record(email, at=NOW)
        return email, conn.execute(
            select(REQUESTS.c.id).where(REQUESTS.c.email == email)
        ).scalar_one()


def test_without_a_password_the_console_is_not_there(committing_engine: Engine) -> None:
    client = browser(committing_engine)
    decide = f"/admin/access/{uuid.uuid4()}/approve"
    for method, path in (
        ("GET", "/admin"),
        ("GET", "/admin/login"),
        ("POST", "/admin/login"),
        ("POST", "/admin/logout"),
        ("POST", decide),
        ("POST", "/admin/signup/open"),
    ):
        assert client.request(method, path).status_code == 404


def test_logging_in_approving_and_logging_out(committing_engine: Engine) -> None:
    client, (email, request_id) = console_client(committing_engine), asked(committing_engine)
    assert client.get("/admin").headers["location"] == "/admin/login"
    login = client.get("/admin/login")
    assert login.status_code == 200 and login.headers["x-robots-tag"] == "noindex, nofollow"
    form = token(login.text)
    assert client.post("/admin/login", data={"password": PASSWORD}).status_code == 403
    wrong = client.post("/admin/login", data={"form_token": form, "password": "guess"})
    assert wrong.status_code == 400 and "That password" in wrong.text
    client.cookies.set("serpsense_admin", "1.forged.signature")
    assert client.get("/admin").headers["location"] == "/admin/login"
    client.cookies.delete("serpsense_admin")
    signed_in = client.post("/admin/login", data={"form_token": form, "password": PASSWORD})
    assert signed_in.status_code == 303 and signed_in.headers["location"] == "/admin"
    cookie = signed_in.headers["set-cookie"].lower()
    assert "serpsense_admin=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/admin/login").headers["location"] == "/admin"  # already in
    home = client.get("/admin")
    assert home.status_code == 200 and email in home.text and "Waiting" in home.text
    assert home.headers["cache-control"] == "no-store"
    csrf = CSRF.findall(home.text)[0]
    approve = f"/admin/access/{request_id}/approve"
    assert client.post(approve, data={"csrf_token": "forged"}).status_code == 403
    assert (
        client.post(f"/admin/access/{request_id}/maybe", data={"csrf_token": csrf}).status_code
        == 404
    )
    assert (
        client.post(f"/admin/access/{uuid.uuid4()}/approve", data={"csrf_token": csrf}).status_code
        == 404
    )
    done = client.post(approve, data={"csrf_token": csrf})
    assert done.status_code == 303 and done.headers["location"] == "/admin"
    with committing_engine.connect() as conn:
        assert SqlAccessRequests(conn).approved(email)
    assert "Approved" in client.get("/admin").text
    copied = client.cookies.get("serpsense_admin")
    assert client.post("/admin/logout", data={}).status_code == 403  # no CSRF token: still in
    assert client.get("/admin").status_code == 200
    out = client.post("/admin/logout", data={"csrf_token": csrf})
    assert out.status_code == 303 and out.headers["location"] == "/admin/login"
    assert client.get("/admin").headers["location"] == "/admin/login"
    client.cookies.set("serpsense_admin", copied or "")  # a copy of the cookie is over too
    assert client.get("/admin").headers["location"] == "/admin/login"
    client.cookies.delete("serpsense_admin")
    after = client.post(approve, data={"csrf_token": csrf})  # a signed-out form decides nothing
    assert after.status_code == 303 and after.headers["location"] == "/admin/login"


def test_the_operator_opens_and_closes_sign_up(fresh_engine: Engine) -> None:
    client = console_client(fresh_engine)
    signed_out = client.post("/admin/signup/open", data={"csrf_token": "x"})
    assert signed_out.headers["location"] == "/admin/login"  # nothing switched
    form = token(client.get("/admin/login").text)
    client.post("/admin/login", data={"form_token": form, "password": PASSWORD})
    page = client.get("/admin").text
    assert "Approval required." in page and "not switched here yet" in page
    assert "default monthly search and AI budgets" in page  # said before opening
    tokens = CSRF.findall(page)
    assert tokens, "the console's forms carry a CSRF token"
    csrf = tokens[0]
    assert client.post("/admin/signup/open", data={"csrf_token": "forged"}).status_code == 403
    assert client.post("/admin/signup/maybe", data={"csrf_token": csrf}).status_code == 404
    opened = client.post("/admin/signup/open", data={"csrf_token": csrf})
    assert opened.status_code == 303 and opened.headers["location"] == "/admin"
    page = client.get("/admin").text
    assert "Open to everyone." in page and "Since " in page
    with fresh_engine.connect() as conn:
        switch = SqlAccessRequests(conn).signup_mode()
    assert switch is not None and switch.mode is SignupMode.OPEN
