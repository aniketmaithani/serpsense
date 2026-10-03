"""The sign-in pages over HTTP, on real Postgres (ADR-0009): forms that came from our page, a
session cookie, and CSRF on sign-out."""

import re
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from serpsense.adapters.db.inbox import SqlInbox
from serpsense.adapters.db.overview import SqlOverview
from serpsense.adapters.db.search_ledger import SqlSearchLedger
from serpsense.adapters.db.stories import SqlStories
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.composition import Container
from serpsense.config import Settings
from serpsense.entrypoints.web.app import create_app
from serpsense.services.accounts import AccountDeletion
from serpsense.services.auth import SignIn, SignInPorts
from serpsense.services.brand_settings import BrandSettings
from serpsense.services.scan_now import ScanNow, ScanNowLimits
from serpsense.services.sessions import SessionGuard
from tests.factories import make_settings
from tests.fakes import FixedClock
from tests.integration.db_helpers import NOW, table
from tests.integration.test_sign_in import BOX, KEYS, OPEN, Jobs, Limiter

pytestmark = [pytest.mark.integration, pytest.mark.api]  # on Postgres, over HTTP

MESSAGES = table("outbox_messages")
TOKEN = re.compile(r'name="(?:form_token|csrf_token)" value="([^"]+)"')


def browser(
    engine: Engine, clock: FixedClock | None = None, settings: Settings | None = None
) -> TestClient:
    clock = clock or FixedClock(NOW)
    ports = SignInPorts(lambda: SqlUnitOfWork(engine, Jobs()), BOX, Limiter(), clock)
    sign_in = SignIn(ports, KEYS, OPEN, session_days=7)
    guard = SessionGuard(ports.unit_of_work, clock, KEYS.csrf, session_days=7)
    unit_of_work = lambda: SqlUnitOfWork(engine, ScanJobs())  # noqa: E731 (one line, one use)
    scan_now = ScanNow(unit_of_work, SqlSearchLedger(engine.begin), clock, ScanNowLimits(20, 240))
    container = Container(
        settings=settings or make_settings(),
        health_checks=(),
        sign_in=sign_in,
        sessions=guard,
        overview=SqlOverview(engine.connect),
        scan_now=scan_now,
        inbox=SqlInbox(engine, clock),
        brand_settings=BrandSettings(
            lambda: SqlUnitOfWork(engine, ScanJobs()), clock, max_searches_per_scan=20
        ),
        stories=SqlStories(engine.connect),
        accounts=AccountDeletion(ports.unit_of_work, sign_in, clock),
    )
    app = create_app(container)
    base = "https://testserver" if settings else "http://testserver"
    return TestClient(app, base_url=base, raise_server_exceptions=False, follow_redirects=False)


class ScanJobs:
    """Scans "Scan now" queued, after their commit."""

    def __init__(self) -> None:
        self.scans: list[uuid.UUID] = []

    def run_scan(self, scan_id: uuid.UUID) -> None:
        self.scans.append(scan_id)

    def dispatch_outbox(self) -> None:
        raise AssertionError("Scan now sends no email")


def token(html: str) -> str:
    match = TOKEN.search(html)
    assert match is not None
    return match.group(1)


def code_for(engine: Engine, email: str) -> str:
    with engine.connect() as conn:
        query = (
            select(MESSAGES.c.sensitive_data_encrypted)
            .where(MESSAGES.c.recipient_email == email)
            .order_by(MESSAGES.c.created_at.desc())
            .limit(1)
        )
        return BOX.open(conn.execute(query).scalar_one()).decode()


def test_signing_in_and_out_through_the_pages(committing_engine: Engine) -> None:
    client, email = browser(committing_engine), f"{uuid.uuid4().hex[:10]}@example.com"
    login = client.get("/login")
    assert (
        login.status_code == 200
        and "default-src 'self'" in login.headers["content-security-policy"]
        and login.headers["cache-control"] == "no-store"
    )
    form = token(login.text)
    assert client.post("/login", data={"email": email}).status_code == 403  # not from our page
    sent = client.post("/login", data={"form_token": form, "email": email})
    assert sent.status_code == 200 and "Check your email" in sent.text and email in sent.text
    assert email not in str(sent.url)  # never in a URL, so never in a log
    wrong = client.post("/verify", data={"form_token": form, "email": email, "code": "abc"})
    assert wrong.status_code == 400 and "That code" in wrong.text
    code = code_for(committing_engine, email)
    signed_in = client.post("/verify", data={"form_token": form, "email": email, "code": code})
    assert signed_in.status_code == 303 and signed_in.headers["location"] == "/"
    cookie = signed_in.headers["set-cookie"].lower()
    assert "serpsense_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    home = client.get("/")
    assert home.status_code == 200 and "Your brands" in home.text
    assert client.get("/login").headers["location"] == "/"  # already signed in
    assert client.post("/logout", data={"csrf_token": "forged"}).status_code == 403
    out = client.post("/logout", data={"csrf_token": token(home.text)})
    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert client.get("/").headers["location"] == "/login"


def test_a_malformed_address_is_said_so(committing_engine: Engine) -> None:
    client = browser(committing_engine)
    form = token(client.get("/login").text)
    response = client.post("/login", data={"form_token": form, "email": "not an address"})
    assert response.status_code == 400 and "look like an email address" in response.text


def test_signing_in_again_ends_the_session_the_browser_had(committing_engine: Engine) -> None:
    clock = FixedClock(NOW)
    client, email = browser(committing_engine, clock), f"{uuid.uuid4().hex[:10]}@example.com"
    form = token(client.get("/login").text)
    for minutes in (0, 2):  # the second time from an old tab, still signed in
        clock.at = NOW + timedelta(minutes=minutes)
        client.cookies.set("serpsense_form", form)
        client.post("/login", data={"form_token": form, "email": email})
        code = code_for(committing_engine, email)
        old = client.cookies.get("serpsense_session")
        client.post("/verify", data={"form_token": form, "email": email, "code": code})
    assert old is not None and client.cookies["serpsense_session"] != old
    client.cookies.set("serpsense_session", old)
    assert client.get("/").headers["location"] == "/login"  # the old session is over


def test_production_cookies_are_secure_and_host_only(committing_engine: Engine) -> None:
    production = make_settings(
        app_env="production", base_url="https://serpsense.example", signup_mode="invite",
        smtp_starttls="true",
    )  # fmt: skip
    cookie = browser(committing_engine, settings=production).get("/login").headers["set-cookie"]
    assert cookie.startswith("__Host-serpsense_form=") and "secure" in cookie.lower()
