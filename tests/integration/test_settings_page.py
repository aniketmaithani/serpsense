"""A brand's search settings over HTTP (BUILD_PLAN §6, §13): preview, save, presets, the owner's
only, and every save a new version."""

import re
import uuid
from datetime import timedelta
from html.parser import HTMLParser
from typing import Any

import pytest
from sqlalchemy import Engine, select

from serpsense.entrypoints.web.settings_form import SECTIONS
from tests.fakes import FixedClock
from tests.integration.db_helpers import NOW, add_brand, add_user, table
from tests.integration.test_login_pages import token
from tests.integration.test_overview_pages import signed_in

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

DOCS, SCHEDULES = table("brand_search_settings_versions"), table("brand_schedule_versions")
LANGUAGES = table("brand_languages")
PER_SCAN = re.compile(r'<span class="big">(\d+)</span> searches a scan')
SHOWN: dict[str, Any] = {  # the page's own values for a brand on the defaults, manual only
    "max_searches": "25", "interval_minutes": "", "languages": "en, hi", "country": "in",
    "google_domain": "google.co.in", "search_pages": "1", "search_templates": "{brand}",
    "autocomplete_prefixes": "{brand} \r\n{brand} is \r\nis {brand} ", "news_terms": "",
    "trends_region": "IN", "trends_range": "today 3-m", "play_review_pages": "1",
    "play_review_sort": "newest", "maps_review_pages": "1", "maps_review_sort": "newest",
    "youtube_templates": "{brand}", "serpapi_cache": "true", "search_page": "true",
    "ai_overview": "true", "autocomplete": "true", "news": "true", "trends": "true",
    "related_queries": "true", "play": "true", "maps": "true",
}  # fmt: skip
FORM: dict[str, Any] = {
    **{key: value for key, value in SHOWN.items() if key not in ("ai_overview", "trends", "maps")},
    "max_searches": "10",
    "interval_minutes": "720",
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


def languages(engine: Engine, brand_id: uuid.UUID) -> set[str]:
    with engine.connect() as conn:
        codes = select(LANGUAGES.c.language_code).where(LANGUAGES.c.brand_id == brand_id)
        return set(conn.execute(codes).scalars())


def latest(engine: Engine, brand_id: uuid.UUID) -> Any:
    with engine.connect() as conn:
        newest = DOCS.c.brand_id == brand_id
        query = select(DOCS.c.document).where(newest).order_by(DOCS.c.created_at.desc())
        return conn.execute(query.limit(1)).scalar_one_or_none()


class SettingsFormReader(HTMLParser):
    """What a browser posts from the page's settings form: its hidden and shown inputs, ticked
    boxes, selected options and textareas."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: list[tuple[str, str]] = []
        self.visible: set[str] = set()
        self._in_form = False
        self._select: str | None = None
        self._textarea: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        if tag == "form":
            self._in_form = a.get("class") == "settings"
        elif self._in_form and tag == "input" and "name" in a:
            self._input(a)
        elif self._in_form and tag in ("select", "textarea"):
            self.visible.add(a["name"])
            self._select, self._textarea = (
                (a["name"], None) if tag == "select" else (None, a["name"])
            )
            if tag == "textarea":
                self.fields.append((a["name"], ""))
        elif self._select and tag == "option" and "selected" in a:
            self.fields.append((self._select, a["value"]))

    def _input(self, a: dict[str, str]) -> None:
        if a.get("type") != "hidden":
            self.visible.add(a["name"])
        if a.get("type") != "checkbox" or "checked" in a:
            self.fields.append((a["name"], a.get("value", "")))

    def handle_data(self, data: str) -> None:
        if self._textarea:
            name, text = self.fields.pop()
            self.fields.append((name, text + data))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("select", "textarea"):
            self._select = self._textarea = None
        elif tag == "form":
            self._in_form = False


def read_form(html: str) -> tuple[dict[str, list[str]], set[str]]:
    reader = SettingsFormReader()
    reader.feed(html)
    posted: dict[str, list[str]] = {}
    for name, value in reader.fields:
        posted.setdefault(name, []).append(value)
    return posted, reader.visible


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
    shown = {**SHOWN, "csrf_token": token(page)}  # the page's own values
    assert client.post(url, data=shown).headers["location"] == f"{url}?saved=unchanged"
    assert versions(committing_engine, ola) == ([], [])  # no layer pinned, no schedule started
    quiet = {key: value for key, value in shown.items() if key != "news"}  # unticked: absent
    client.post(url, data={**quiet, "interval_minutes": "720"})
    docs, intervals = versions(committing_engine, ola)
    assert docs == [{"news": {"enabled": False}}] and intervals == [720]  # only what changed
    clock.at = NOW + timedelta(minutes=1)
    assert client.post(url, data=quiet).headers["location"].endswith("saved=saved")
    assert versions(committing_engine, ola)[1] == [720, None]  # back to manual


def test_every_search_knob_and_the_languages_save_and_show_again(
    committing_engine: Engine,
) -> None:
    client, owner = signed_in(committing_engine)
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
    url = f"/brands/{ola}/settings"
    changed = {
        **SHOWN, "csrf_token": token(client.get(url).text), "max_searches": "40",
        "languages": "EN, hi ta", "google_domain": "google.com", "search_pages": "2",
        "search_templates": "{brand}\r\n{brand} reviews\r\n\r\n",
        "autocomplete_prefixes": "{brand} \r\n{brand} vs ", "news_terms": "complaint\r\noutage",
        "trends_region": "IN-KA", "trends_range": "today 12-m",
        "play_review_sort": "most_relevant", "maps_review_pages": "2",
        "maps_review_sort": "most_relevant", "youtube": "true",
        "youtube_templates": "{brand} review",
    }  # fmt: skip
    for unticked in ("serpapi_cache", "related_queries"):
        del changed[unticked]
    preview = client.post(url, data={**changed, "action": "preview"})
    assert preview.status_code == 200 and "Languages: en, hi, ta." in preview.text
    assert client.post(url, data=changed).headers["location"] == f"{url}?saved=saved"
    assert versions(committing_engine, ola)[0] == [  # only what differs from the defaults
        {
            "max_searches": 40,
            "serpapi_cache": False,
            "google_domain": "google.com",
            "search_page": {"pages": 2, "templates": ["{brand}", "{brand} reviews"]},
            "autocomplete": {"prefixes": ["{brand} ", "{brand} vs "]},
            "news": {"extra_terms": ["complaint", "outage"]},
            "trends": {"region": "IN-KA", "date_range": "today 12-m", "related_queries": False},
            "play": {"review_sort": "most_relevant"},
            "maps": {"review_pages": 2, "review_sort": "most_relevant"},
            "youtube": {"enabled": True, "templates": ["{brand} review"]},
        }
    ]
    assert languages(committing_engine, ola) == {"en", "hi", "ta"}
    general = client.get(url).text
    assert 'name="languages" value="en, hi, ta"' in general and 'value="google.com"' in general
    assert 'name="serpapi_cache" value="true">' in general  # unticked
    trends = client.get(f"{url}?section=trends").text
    assert '<option value="today 12-m" selected>Past 12 months</option>' in trends
    reviews = client.get(f"{url}?section=reviews").text
    assert '<option value="most_relevant" selected>Most relevant</option>' in reviews
    search = client.get(f"{url}?section=search").text
    assert ">{brand}\n{brand} reviews</textarea>" in search
    assert 'name="youtube" value="true" checked' in client.get(f"{url}?section=youtube").text
    assert client.post(url, data=changed).headers["location"].endswith("saved=unchanged")


def test_saving_one_section_keeps_every_other_section(committing_engine: Engine) -> None:
    clock = FixedClock(NOW)
    client, owner = signed_in(committing_engine, clock)
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
    url = f"/brands/{ola}/settings"
    first = {
        **SHOWN, "csrf_token": token(client.get(url).text), "languages": "en, ta",
        "search_pages": "2", "autocomplete_prefixes": "{brand} \r\n{brand} vs ",
        "youtube": "true", "maps_review_sort": "most_relevant",
    }  # fmt: skip
    assert client.post(url, data=first).headers["location"].endswith("saved=saved")
    before = latest(committing_engine, ola)
    for minutes, section in enumerate(SECTIONS, start=1):
        clock.at = NOW + timedelta(minutes=minutes)  # a version a save may write is a new one
        page = client.get(f"{url}?section={section.value}").text
        posted, visible = read_form(page)
        shown = {name for name in SECTIONS[section][1] if name in visible}
        assert shown and visible - {"csrf_token"} <= set(SECTIONS[section][1])  # one section
        saved = client.post(url, data=posted)
        assert "saved=unchanged" in saved.headers["location"], section  # nothing lost
    clock.at = NOW + timedelta(hours=1)
    page = client.get(f"{url}?section=trends").text
    posted, _ = read_form(page)
    posted["trends_range"] = ["today 12-m"]
    saved = client.post(url, data=posted)
    assert saved.headers["location"] == f"{url}?saved=saved&section=trends"
    assert latest(committing_engine, ola) == {**before, "trends": {"date_range": "today 12-m"}}
    assert languages(committing_engine, ola) == {"en", "ta"}


def test_bad_knobs_and_languages_are_refused_and_text_is_escaped(
    committing_engine: Engine,
) -> None:
    client, owner = signed_in(committing_engine)
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
    url = f"/brands/{ola}/settings"
    form = {**SHOWN, "csrf_token": token(client.get(url).text)}
    assert client.post(url, data=form).headers["location"].endswith("saved=unchanged")
    hostile = "<script>alert(1)</script>"
    for bad in (
        {"search_templates": f"{hostile} no placeholder"},
        {"search_templates": "{brand}\n{brand} a\n{brand} b\n{brand} c"},  # four: at most three
        {"autocomplete_prefixes": "{brand} {other}"},
        {"youtube_templates": ""},
        {"trends_region": "India"},
        {"languages": ""},
        {"languages": "en, en"},
        {"languages": f"en, {hostile}"},
    ):
        preview = client.post(url, data={**form, **bad, "action": "preview"})
        assert preview.status_code == 400 and "be used for a scan" in preview.text, bad
        assert hostile not in preview.text
        assert client.post(url, data={**form, **bad}).headers["location"].endswith("invalid")
    assert client.post(url, data={**form, "trends_range": "now 9-y"}).status_code == 400
    assert versions(committing_engine, ola)[0] == [] and languages(committing_engine, ola) == set()

    named = {**form, "search_templates": f"{{brand}} {hostile}"}
    assert client.post(url, data=named).headers["location"].endswith("saved=saved")
    page = client.get(f"{url}?section=search").text
    assert hostile not in page and "{brand} &lt;script&gt;alert(1)&lt;/script&gt;" in page
