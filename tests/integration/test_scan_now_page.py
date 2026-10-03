"""Scan now over HTTP (BUILD_PLAN §13): a CSRF-checked form on the owner's brand page."""

import uuid

import pytest
from sqlalchemy import Engine, select

from tests.integration.db_helpers import add_brand, add_user, table
from tests.integration.test_login_pages import browser, token
from tests.integration.test_overview_pages import signed_in

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

SCANS = table("scans")


def test_scan_now_queues_one_manual_scan_for_the_owner(committing_engine: Engine) -> None:
    client, owner = signed_in(committing_engine)
    slug = uuid.uuid4().hex[:8]
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{slug}")
        stranger = add_user(conn, f"{slug}@example.com")
        theirs = add_brand(conn, stranger, name="Theirs", slug=f"theirs-{slug}")
    page = client.get(f"/brands/{ola}")
    assert "Scan now" in page.text
    form = token(page.text)
    assert client.post(f"/brands/{ola}/scan", data={"csrf_token": "forged"}).status_code == 403

    queued = client.post(f"/brands/{ola}/scan", data={"csrf_token": form})
    assert queued.status_code == 303 and queued.headers["location"] == f"/brands/{ola}?scan=queued"
    shown = client.get(queued.headers["location"]).text
    assert "Scan queued" in shown and "Scanning" in shown and 'action="/brands/' not in shown
    again = client.post(f"/brands/{ola}/scan", data={"csrf_token": form})
    assert again.headers["location"] == f"/brands/{ola}?scan=too_soon"
    with committing_engine.connect() as conn:
        rows = conn.execute(select(SCANS).where(SCANS.c.brand_id == ola)).mappings().all()
    assert [(r["trigger"], r["requested_by"], r["status"]) for r in rows] == [
        ("manual", owner, "queued")
    ]

    for missing in (theirs, uuid.uuid4(), "not-an-id"):
        response = client.post(f"/brands/{missing}/scan", data={"csrf_token": form})
        assert response.status_code == 404
    with committing_engine.connect() as conn:
        assert conn.execute(select(SCANS.c.id).where(SCANS.c.brand_id == theirs)).first() is None
    signed_out = browser(committing_engine).post(f"/brands/{ola}/scan", data={"csrf_token": form})
    assert signed_out.headers["location"] == "/login"


def test_an_unknown_outcome_in_the_address_says_nothing(committing_engine: Engine) -> None:
    client, owner = signed_in(committing_engine)
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
    page = client.get(f"/brands/{ola}?scan=<script>").text
    assert 'class="notice"' not in page and "<script>" not in page
