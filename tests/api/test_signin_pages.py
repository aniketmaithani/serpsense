"""The sign-in pages in the landing page's look (issue #183): the same fonts and colours, no
scripts at all, a way back to the landing page, and the form unchanged."""

import re

import pytest
from fastapi.testclient import TestClient

from serpsense.entrypoints.web.app import create_app
from tests.api.test_landing import STYLE_ATTRIBUTE
from tests.factories import make_container, make_settings

pytestmark = pytest.mark.api

TOKEN = re.compile(r'name="form_token" value="([^"]+)"')


def visitor() -> TestClient:
    return TestClient(create_app(make_container(make_settings())), follow_redirects=False)


def test_the_sign_in_page_wears_the_landing_page_look() -> None:
    page = visitor().get("/login")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    for sheet in ("tokens.css", "signin.css"):
        assert f'<link rel="stylesheet" href="/static/landing/{sheet}">' in page.text
    assert '<a class="s-back" href="/">Back to the landing page</a>' in page.text
    assert "Not affiliated with SerpApi, LLC." in page.text
    assert "Created by Aniket Maithani." in page.text
    assert "<script" not in page.text and not STYLE_ATTRIBUTE.search(page.text)
    assert '<svg class="orbit" viewBox="0 0 640 360" aria-hidden="true">' in page.text
    assert '<form method="post" action="/login">' in page.text and TOKEN.search(page.text)
    assert 'name="email" type="email" autocomplete="email" required' in page.text


def test_a_deleted_account_is_told_so_politely() -> None:
    page = visitor().get("/login?deleted=1").text
    assert '<p class="notice" role="status">Your account was deleted.</p>' in page


def test_a_malformed_address_marks_the_field_and_says_why() -> None:
    client = visitor()
    form = TOKEN.search(client.get("/login").text)
    assert form is not None
    answer = client.post("/login", data={"form_token": form[1], "email": "not an address"})
    assert answer.status_code == 400
    assert '<p class="error" id="problem" role="alert">' in answer.text
    assert 'aria-invalid="true" aria-describedby="problem"' in answer.text
    assert 'value="not an address"' in answer.text  # kept, to correct


def test_replay_mode_says_on_every_page_that_the_demo_is_fictional() -> None:
    said = "VoltBox and SoundNest are fictional"
    replay = TestClient(create_app(make_container(make_settings(serpsense_mode="replay"))))
    assert said in replay.get("/login").text  # and in base.html, on every signed-in page
    assert said not in visitor().get("/login").text  # live mode shows real brands only
