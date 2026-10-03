"""The brands list and a brand's page over HTTP (BUILD_PLAN §13): the owner's only."""

import hashlib
import html
import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, select

from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.domain.enums import CrisisComponent, Surface, Topic
from serpsense.ports.enrichment_store import MentionLabel
from tests.fakes import FixedClock
from tests.integration.db_helpers import (
    NOW,
    add_brand,
    add_llm_call,
    add_mention,
    add_scan,
    add_user,
    table,
)
from tests.integration.test_login_pages import browser, code_for, token
from tests.integration.test_scan_scores_view import scored

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

CALM = {c: 0 for c in CrisisComponent}
PROMPT = "label_mentions/v1"


def signed_in(engine: Engine, clock: FixedClock | None = None) -> tuple[TestClient, uuid.UUID]:
    client, email = browser(engine, clock), f"{uuid.uuid4().hex[:10]}@example.com"
    form = token(client.get("/login").text)
    client.post("/login", data={"form_token": form, "email": email})
    client.post(
        "/verify", data={"form_token": form, "email": email, "code": code_for(engine, email)}
    )
    with engine.connect() as conn:
        users = table("users")
        return client, conn.execute(select(users.c.id).where(users.c.email == email)).scalar_one()


HOSTILE = '<script>alert(1)</script>"><img src=x onerror=alert(1)>'
TREND = re.compile(r"data-trend='([^']*)'")


def test_the_owner_sees_their_brands_and_a_brand_page(committing_engine: Engine) -> None:
    assert "Not affiliated with SerpApi" in browser(committing_engine).get("/").text  # signed out
    client, owner = signed_in(committing_engine)
    slug = uuid.uuid4().hex[:8]
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{slug}")
        uber = add_brand(conn, owner, name="Uber", slug=f"uber-{slug}")
        rivals = table("brand_competitors")
        conn.execute(insert(rivals).values(brand_id=ola, competitor_brand_id=uber))
        scan = add_scan(conn, ola, status="succeeded")
        scored(conn, scan, {Surface.NEWS: 62}, CALM)
        stranger = add_user(conn, f"{slug}@example.com")
        theirs = add_brand(conn, stranger, name="Theirs", slug=f"theirs-{slug}")
        archived = add_brand(conn, owner, name="Old", slug=f"old-{slug}", archived_at=NOW)
    home = client.get("/")
    assert home.status_code == 200 and "Ola" in home.text and "competitor of Ola" in home.text
    assert "Theirs" not in home.text and "Old" not in home.text  # another's, and archived
    assert '<footer class="site-foot">Created by Aniket Maithani.' in home.text
    page = client.get(f"/brands/{ola}")
    assert page.status_code == 200 and "warming up" in page.text and "Uber" in page.text
    trend = TREND.search(page.text)
    assert trend is not None and [
        p["health"] for p in json.loads(html.unescape(trend.group(1)))
    ] == [62]
    assert "/static/vendor/chart.umd.js" in page.text
    assert (
        page.headers["cache-control"] == "no-store"
        and "frame-ancestors 'none'" in page.headers["content-security-policy"]
    )
    answers = {client.get(f"/brands/{m}").text for m in (theirs, archived, uuid.uuid4(), "x")}
    assert answers == {client.get(f"/brands/{uuid.uuid4()}").text}  # one 404 for all of them
    assert client.get(f"/brands/{theirs}").status_code == 404
    assert browser(committing_engine).get(f"/brands/{ola}").headers["location"] == "/login"


def test_what_search_results_say_is_shown_escaped(committing_engine: Engine) -> None:
    client, owner = signed_in(committing_engine)
    with committing_engine.begin() as conn:
        brand_id = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
        scan = add_scan(conn, brand_id, status="succeeded")
        scored(conn, scan, {Surface.NEWS: 50}, CALM)
        key = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        hostile = {"identity_key": key, "text": HOSTILE, "outlet": HOSTILE}
        mention = add_mention(conn, brand_id, **hostile)
        conn.execute(insert(table("mention_observations")).values(mention_id=mention, scan_id=scan))
        said = MentionLabel(mention, 1, -1, 50, Topic.RELIABILITY, True, True, HOSTILE)
        call = add_llm_call(conn, owner)  # the model's words are untrusted too
        SqlEnrichmentStore(conn).record([said], prompt_version=PROMPT, llm_call_id=call, at=NOW)
        rival = add_brand(conn, owner, name=HOSTILE, slug=f"x-{uuid.uuid4().hex[:8]}")
        conn.execute(
            insert(table("brand_competitors")).values(brand_id=brand_id, competitor_brand_id=rival)
        )
    page = client.get(f"/brands/{brand_id}").text
    assert HOSTILE not in page and "<script>alert" not in page and "&lt;script&gt;" in page
    assert page.count("&lt;script&gt;") >= 4  # text, outlet, reason and the rival's name
    assert HOSTILE not in client.get("/").text
