"""How a brand's crisis is picked up, its tuning page, and its mentions in pages, over HTTP: the
owner's only, CSRF-checked, refused when out of range."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, insert

from serpsense.domain.enums import CrisisComponent, Surface
from tests.fakes import FixedClock
from tests.integration.db_helpers import NOW, add_brand, add_mention, add_scan, table
from tests.integration.test_login_pages import browser, token
from tests.integration.test_overview_pages import signed_in
from tests.integration.test_scan_scores_view import scored

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

SPIKE = {CrisisComponent.VELOCITY: 90, CrisisComponent.PRESS: 75}  # crisis 35
TUNED = {"warm_up_scans": "0", "medium_at": "30", "high_at": "60"}
TUNED |= {"cooldown_hours": "6", "spread_mentions": "4", "spread_surfaces": "2"}


def _brand(engine: Engine, owner: uuid.UUID, mentions: int = 0) -> uuid.UUID:
    with engine.begin() as conn:
        brand_id = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
        scan = add_scan(conn, brand_id, status="succeeded")
        scored(conn, scan, {Surface.NEWS: 50}, SPIKE)
        for n in range(mentions):
            key = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            mention = add_mention(conn, brand_id, identity_key=key, text=f"Mention number {n:02}")
            observed = {"mention_id": mention, "scan_id": scan, "position": n + 1}
            conn.execute(insert(table("mention_observations")).values(**observed))
    return brand_id


def test_the_brand_page_says_how_its_crisis_is_picked_up(committing_engine: Engine) -> None:
    client, owner = signed_in(committing_engine)
    page = client.get(f"/brands/{_brand(committing_engine, owner)}").text
    assert "How the crisis is picked up" in page and "warming up: 0 of 3" in page
    assert '<td class="num">30%</td><td class="num">90</td><td class="num">27</td>' in page
    assert "medium from 40 to 69, high from 70" in page and "rests 12 h" in page


def test_the_owner_tunes_the_crisis_and_the_level_follows(committing_engine: Engine) -> None:
    clock = FixedClock(NOW)
    client, owner = signed_in(committing_engine, clock)
    brand_id = _brand(committing_engine, owner)
    page = client.get(f"/brands/{brand_id}/crisis")
    assert page.status_code == 200 and 'name="medium_at"' in page.text
    form = {"csrf_token": token(page.text), **TUNED}
    assert (
        client.post(f"/brands/{brand_id}/crisis", data={**form, "csrf_token": "x"}).status_code
        == 403
    )
    saved = client.post(f"/brands/{brand_id}/crisis", data=form)
    assert saved.headers["location"].endswith("?saved=saved")
    brand_page = client.get(f"/brands/{brand_id}").text
    assert (
        'level-medium">medium' in brand_page and "medium from 30 to 59, high from 60" in brand_page
    )
    again = client.post(f"/brands/{brand_id}/crisis", data=form)
    assert again.headers["location"].endswith("?saved=unchanged")

    for bad in ({"high_at": "30"}, {"warm_up_scans": "9"}, {"cooldown_hours": "0"}):
        refused = client.post(f"/brands/{brand_id}/crisis", data={**form, **bad})
        assert refused.status_code == 400 and "Not saved" in refused.text, bad
    assert 'value="30"' in client.get(f"/brands/{brand_id}/crisis").text  # still the saved one

    clock.at += timedelta(minutes=1)  # one version per instant
    reset = client.post(f"/brands/{brand_id}/crisis", data={**form, "action": "reset"})
    assert reset.headers["location"].endswith("?saved=saved")
    assert "warming up" in client.get(f"/brands/{brand_id}").text


def test_only_the_owner_reaches_the_tuning(committing_engine: Engine) -> None:
    _, owner = signed_in(committing_engine)
    brand_id = _brand(committing_engine, owner)
    other, _ = signed_in(committing_engine)
    assert other.get(f"/brands/{brand_id}/crisis").status_code == 404
    form = {"csrf_token": token(other.get("/").text), **TUNED}
    assert other.post(f"/brands/{brand_id}/crisis", data=form).status_code == 404
    assert other.get("/brands/not-an-id/crisis").status_code == 404
    outsider = browser(committing_engine)
    assert outsider.get(f"/brands/{brand_id}/crisis").headers["location"] == "/login"


def test_mentions_come_in_pages(committing_engine: Engine) -> None:
    client, owner = signed_in(committing_engine)
    brand_id = _brand(committing_engine, owner, mentions=23)
    first = client.get(f"/brands/{brand_id}").text
    assert "Page 1 of 3" in first and "Mention number 00" in first
    assert "Mention number 10" not in first and 'rel="next"' in first and 'rel="prev"' not in first
    last = client.get(f"/brands/{brand_id}?page=3").text
    assert "Page 3 of 3" in last and "Mention number 22" in last and "Mention number 19" not in last
    assert 'rel="prev"' in last and 'rel="next"' not in last
    assert "Page 3 of 3" in client.get(f"/brands/{brand_id}?page=99").text  # past the last
    assert "Page 1 of 3" in client.get(f"/brands/{brand_id}?page=0").text
    few = client.get(f"/brands/{_brand(committing_engine, owner, mentions=4)}").text
    assert "Page 1 of" not in few and "Mention number 03" in few
