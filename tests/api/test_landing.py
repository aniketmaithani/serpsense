"""The public landing page (issue #183): anonymous visitors get the story at `/`, as plain HTML
that reads without JavaScript, under the same security headers as every page."""

import re
import uuid
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from serpsense.entrypoints.web.app import create_app
from serpsense.services.sessions import CurrentUser
from tests.factories import make_container, make_settings

pytestmark = pytest.mark.api

HEADLINE = "Your brand's reputation is whatever Google shows people."
CHAPTERS = ["hero", "surfaces", "collect", "normalise", "enrich", "narratives", "score"]
CHAPTERS += ["alert", "draft", "replay", "built-right", "cta"]
SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL)
STYLE_ATTRIBUTE = re.compile(r"<[^>]*\sstyle\s*=", re.IGNORECASE)
OUTSIDE = re.compile(r'(?:href|src)="((?:https?:)?//[^"]+)"')


def test_an_anonymous_visitor_gets_the_landing_page() -> None:
    client = TestClient(create_app(make_container(make_settings())), follow_redirects=False)
    page = client.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"  # `/` differs once you're signed in
    assert HEADLINE in page.text and "Not affiliated with SerpApi, LLC." in page.text
    assert "VoltBox" in page.text and "SoundNest" in page.text  # fictional brands only
    assert "script-src 'self'" in page.headers["content-security-policy"]
    scripts = SCRIPT.findall(page.text)
    assert page.text.count("<script") == len(scripts)
    assert all(' src="/static/' in attributes and not body for attributes, body in scripts)
    assert not STYLE_ATTRIBUTE.search(page.text)  # nothing inline, in either quote style
    assert all(f'id="{chapter}"' in page.text for chapter in CHAPTERS)
    assert 'href="https://github.com/aniketmaithani/serpsense"' in page.text
    assert {urlsplit(url).netloc for url in OUTSIDE.findall(page.text)} == {"github.com"}


class OneSession:
    """Every session cookie opens the same user's session; nothing touches a database."""

    user = CurrentUser(user_id=uuid.uuid4(), session_id=uuid.uuid4(), csrf_secret=b"secret")

    def current(self, token: str | None) -> CurrentUser | None:
        return self.user if token else None

    def csrf_token(self, user: CurrentUser) -> str:
        return "csrf"


class NoBrands:
    def brands(self, user_id: uuid.UUID) -> list[object]:
        return []


class NothingUnread:
    def unread(self, user_id: uuid.UUID) -> int:
        return 0


def test_a_signed_in_visitor_still_gets_their_brands() -> None:
    container = make_container(make_settings())
    stubs = {"sessions": OneSession(), "overview": NoBrands(), "inbox": NothingUnread()}
    client = TestClient(create_app(replace(container, **stubs)))
    client.cookies.set("serpsense_session", "token")
    page = client.get("/")
    assert page.status_code == 200 and "<h1>Your brands</h1>" in page.text
    assert HEADLINE not in page.text and page.headers["cache-control"] == "no-store"
