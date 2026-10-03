"""Adding a brand over HTTP: the brand, its app, its schedule and its competitors in one go,
refused when invalid or forged, and only for the signed-in owner."""

import uuid

import pytest
from sqlalchemy import Engine, func, select

from tests.integration.db_helpers import table
from tests.integration.test_login_pages import browser, token
from tests.integration.test_overview_pages import signed_in

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]


def test_the_owner_adds_a_brand_with_an_app_and_competitors(committing_engine: Engine) -> None:
    client, owner = signed_in(committing_engine)
    assert 'href="/brands/new"' in client.get("/").text
    page = client.get("/brands/new")
    assert page.status_code == 200
    name = f"Volt {uuid.uuid4().hex[:6]}"
    form = {
        "csrf_token": token(page.text),
        "name": name,
        "play_app": "com.volt.app",
        "competitors": f"Amp {name}\nWatt {name}\n",
        "preset": "lean",
        "interval_minutes": "360",
    }
    assert client.post("/brands/new", data={**form, "csrf_token": "forged"}).status_code == 403
    added = client.post("/brands/new", data=form)
    assert added.status_code == 303 and added.headers["location"].endswith("?scan=added")
    assert "Brand added" in client.get(added.headers["location"]).text
    home = client.get("/").text
    assert name in home and home.count(f"competitor of {name}") == 2
    with committing_engine.connect() as conn:
        brands, rivals = table("brands"), table("brand_competitors")
        owned = select(func.count()).select_from(brands).where(brands.c.owner_id == owner)
        assert conn.execute(owned).scalar_one() == 3
        assert conn.execute(select(func.count()).select_from(rivals)).scalar_one() >= 2


def test_invalid_brands_are_refused_and_signed_out_visitors_sent_to_sign_in(
    committing_engine: Engine,
) -> None:
    client, _ = signed_in(committing_engine)
    form = {"csrf_token": token(client.get("/brands/new").text), "name": "Volt", "preset": "lean"}
    for bad in (
        {"name": "  "},
        {"play_app": "not an app id"},
        {"competitors": "A\nB\nC\nD\nE"},
        {"competitors": "Volt"},
        {"interval_minutes": "7"},
    ):
        assert client.post("/brands/new", data={**form, **bad}).status_code == 400, bad
    outsider = browser(committing_engine)
    assert outsider.get("/brands/new").headers["location"] == "/login"
    assert outsider.post("/brands/new", data=form).headers["location"] == "/login"
