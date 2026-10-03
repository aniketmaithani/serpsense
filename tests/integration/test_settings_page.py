"""A brand's search settings over HTTP (BUILD_PLAN §6, §13): preview, save, presets, the owner's
only, and every save a new version."""

import re
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select

from tests.fakes import FixedClock
from tests.integration.db_helpers import NOW, add_brand, add_user, table
from tests.integration.test_login_pages import token
from tests.integration.test_overview_pages import signed_in

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

DOCS, SCHEDULES = table("brand_search_settings_versions"), table("brand_schedule_versions")
PER_SCAN = re.compile(r'<span class="big">(\d+)</span> searches a scan')
FORM: dict[str, Any] = {
    "max_searches": "10",
    "interval_minutes": "720",
    "play_review_pages": "1",
    "search_page": "true",
    "autocomplete": "true",
    "news": "true",
    "play": "true",
}


def per_scan(html: str) -> int:
    match = PER_SCAN.search(html)
    assert match is not None
    return int(match.group(1))


def versions(engine: Engine, brand_id: uuid.UUID) -> tuple[list[Any], list[Any]]:
    with engine.connect() as conn:
        docs = conn.execute(select(DOCS.c.document).where(DOCS.c.brand_id == brand_id))
        times = conn.execute(
            select(SCHEDULES.c.interval_minutes).where(SCHEDULES.c.brand_id == brand_id)
        )
        return list(docs.scalars()), list(times.scalars())


def test_the_owner_previews_saves_and_starts_from_a_preset(committing_engine: Engine) -> None:
    clock = FixedClock(NOW)
    client, owner = signed_in(committing_engine, clock)
    slug = uuid.uuid4().hex[:8]
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{slug}")
    page = client.get(f"/brands/{ola}/settings")
    assert page.status_code == 200 and "Search settings" in page.text
    form = {**FORM, "csrf_token": token(page.text)}
    url = f"/brands/{ola}/settings"

    preview = client.post(url, data={**form, "action": "preview"})
    assert preview.status_code == 200 and "Preview: not saved" in preview.text
    # search page 1, autocomplete 3 prefixes x 2 languages, news 2, no Play app: 9 under 10.
    assert per_scan(preview.text) == 9 and versions(committing_engine, ola) == ([], [])

    saved = client.post(url, data=form)
    assert saved.headers["location"] == f"{url}?saved=saved"
    docs, intervals = versions(committing_engine, ola)
    assert docs[0]["max_searches"] == 10 and docs[0]["maps"] == {"enabled": False}
    assert intervals == [720]
    again = client.post(url, data=form)
    assert again.headers["location"] == f"{url}?saved=unchanged"
    assert "Nothing changed" in client.get(again.headers["location"]).text

    clock.at = NOW + timedelta(minutes=1)  # a version a minute later
    lean = client.post(f"{url}/preset", data={"csrf_token": form["csrf_token"], "preset": "lean"})
    assert lean.headers["location"] == f"{url}?saved=saved"
    docs, intervals = versions(committing_engine, ola)
    assert len(docs) == 2 and {"languages": ["en"]}.items() <= docs[-1].items()
    assert intervals == [720]  # a preset keeps the schedule


def test_bad_values_forged_forms_and_other_brands_change_nothing(
    committing_engine: Engine,
) -> None:
    client, owner = signed_in(committing_engine)
    slug = uuid.uuid4().hex[:8]
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{slug}")
        theirs = add_brand(conn, add_user(conn, f"{slug}@x.in"), name="T", slug=f"t-{slug}")
    form = {**FORM, "csrf_token": token(client.get(f"/brands/{ola}/settings").text)}
    url = f"/brands/{ola}/settings"
    assert client.post(url, data={**form, "csrf_token": "forged"}).status_code == 403
    odd = client.post(url, data={**form, "interval_minutes": "5", "action": "preview"})
    assert odd.status_code == 400 and "be used for a scan" in odd.text
    assert (
        client.post(url, data={**form, "interval_minutes": "5"})
        .headers["location"]
        .endswith("saved=invalid")
    )
    zero = client.post(url, data={**form, "max_searches": "0"})
    assert zero.headers["location"].endswith("saved=invalid")
    incomplete = client.post(url, data={"csrf_token": form["csrf_token"]})
    assert incomplete.status_code == 400 and form["csrf_token"] not in incomplete.text
    forged = {"csrf_token": "forged", "preset": "deep"}
    assert client.post(f"{url}/preset", data=forged).status_code == 403
    assert versions(committing_engine, ola) == ([], [])
    for other in (theirs, uuid.uuid4(), "not-an-id"):
        assert client.get(f"/brands/{other}/settings").status_code == 404
        assert client.post(f"/brands/{other}/settings", data=form).status_code == 404
        preset = {"csrf_token": form["csrf_token"], "preset": "deep"}
        assert client.post(f"/brands/{other}/settings/preset", data=preset).status_code == 404
    assert versions(committing_engine, theirs) == ([], [])
    client.cookies.clear()
    assert client.post(url, data=form).headers["location"] == "/login"
    preset = {"csrf_token": form["csrf_token"], "preset": "lean"}
    assert client.post(f"{url}/preset", data=preset).headers["location"] == "/login"
    assert client.get(url).headers["location"] == "/login"


def test_a_manual_brand_stays_manual_until_its_owner_picks_an_interval(
    committing_engine: Engine,
) -> None:
    clock = FixedClock(NOW)
    client, owner = signed_in(committing_engine, clock)
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
    url = f"/brands/{ola}/settings"
    page = client.get(url).text
    assert '<option value="" selected>Manual only</option>' in page and "Scan now" in page
    shown = {  # the page's own values: the defaults, manual only
        "csrf_token": token(page), "max_searches": "25", "interval_minutes": "",
        "play_review_pages": "1", "search_page": "true", "ai_overview": "true",
        "autocomplete": "true", "news": "true", "trends": "true", "play": "true", "maps": "true",
    }  # fmt: skip
    assert client.post(url, data=shown).headers["location"] == f"{url}?saved=unchanged"
    assert versions(committing_engine, ola) == ([], [])  # no layer pinned, no schedule started
    quiet = {key: value for key, value in shown.items() if key != "news"}  # unticked: absent
    client.post(url, data={**quiet, "interval_minutes": "720"})
    docs, intervals = versions(committing_engine, ola)
    assert docs == [{"news": {"enabled": False}}] and intervals == [720]  # only what changed
    clock.at = NOW + timedelta(minutes=1)
    assert client.post(url, data=quiet).headers["location"].endswith("saved=saved")
    assert versions(committing_engine, ola)[1] == [720, None]  # back to manual
