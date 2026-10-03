"""Saving a brand's settings on Postgres: versions only for changes, schedules and presets."""

import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select

from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.domain.settings.search import Preset, SearchSettings
from serpsense.services.brand_settings import BrandSettings, Knobs, Saved
from tests.fakes import FixedClock, RecordingJobs
from tests.integration.db_helpers import NOW, add, add_brand, add_user, table

pytestmark = pytest.mark.integration

DOCS, SCHEDULES = table("brand_search_settings_versions"), table("brand_schedule_versions")
DEFAULTS = Knobs.of(SearchSettings(), None)


def versions(engine: Engine, brand_id: uuid.UUID) -> tuple[list[Any], list[Any]]:
    with engine.connect() as conn:
        docs = conn.execute(
            select(DOCS.c.document).where(DOCS.c.brand_id == brand_id).order_by(DOCS.c.created_at)
        )
        times = conn.execute(
            select(SCHEDULES.c.interval_minutes, SCHEDULES.c.timezone)
            .where(SCHEDULES.c.brand_id == brand_id)
            .order_by(SCHEDULES.c.created_at)
        )
        return list(docs.scalars()), [tuple(row) for row in times]


def test_saves_write_versions_only_for_what_changed(committing_engine: Engine) -> None:
    clock = FixedClock(NOW)
    settings = BrandSettings(
        lambda: SqlUnitOfWork(committing_engine, RecordingJobs()), clock, max_searches_per_scan=20
    )
    slug = uuid.uuid4().hex[:8]
    with committing_engine.begin() as conn:
        owner = add_user(conn, f"{slug}@example.com")
        brand = add_brand(conn, owner, name="Ola", slug=f"ola-{slug}")
    save = lambda knobs: settings.save(owner, brand, knobs)  # noqa: E731 (a short local alias)

    assert save(DEFAULTS) is Saved.UNCHANGED  # the page's own values: nothing to write
    assert versions(committing_engine, brand) == ([], [])
    quieter = replace(DEFAULTS, news=False, interval_minutes=720)
    assert save(quieter) is Saved.SAVED
    daily = (720, "Asia/Kolkata")
    assert versions(committing_engine, brand) == ([{"news": {"enabled": False}}], [daily])
    assert save(quieter) is Saved.UNCHANGED

    clock.at = NOW + timedelta(minutes=1)
    assert save(replace(quieter, interval_minutes=None)) is Saved.SAVED  # back to manual only
    assert versions(committing_engine, brand)[1] == [daily, (None, "Asia/Kolkata")]

    assert settings.apply_preset(owner, brand, Preset.LEAN) is Saved.SAVED
    docs, schedules = versions(committing_engine, brand)
    assert docs[-1]["languages"] == ["en"] and len(schedules) == 2  # the schedule is kept

    assert save(replace(DEFAULTS, max_searches=0)) is Saved.INVALID
    assert save(replace(DEFAULTS, interval_minutes=5)) is Saved.INVALID
    assert settings.save(uuid.uuid4(), brand, DEFAULTS) is Saved.MISSING
    assert len(versions(committing_engine, brand)[0]) == 2  # nothing more was written


def test_a_save_keeps_only_what_differs_from_the_owners_defaults(
    committing_engine: Engine,
) -> None:
    settings = BrandSettings(
        lambda: SqlUnitOfWork(committing_engine, RecordingJobs()),
        FixedClock(NOW),
        max_searches_per_scan=20,
    )
    slug = uuid.uuid4().hex[:8]
    defaults = {"news": {"extra_terms": ["complaint"]}, "languages": ["en"]}
    with committing_engine.begin() as conn:
        owner = add_user(conn, f"{slug}@example.com")
        brand = add_brand(conn, owner, name="Ola", slug=f"ola-{slug}")
        add(conn, table("user_search_default_versions"), user_id=owner, document=defaults,
            schema_version=1, created_at=NOW)  # fmt: skip
    view = settings.view(owner, brand)
    assert view is not None and view.knobs.news_terms == ("complaint",)
    assert view.knobs.languages == ("en",)
    assert settings.save(owner, brand, view.knobs) is Saved.UNCHANGED  # the owner's own
    assert own_languages(committing_engine, brand) == set()  # still following the owner's

    wider = replace(view.knobs, news_terms=(), youtube=True, maps_review_pages=2)
    assert settings.save(owner, brand, replace(wider, languages=("hi", "en"))) is Saved.SAVED
    assert versions(committing_engine, brand)[0] == [
        {"news": {"extra_terms": []}, "maps": {"review_pages": 2}, "youtube": {"enabled": True}}
    ]
    assert own_languages(committing_engine, brand) == {"en", "hi"}
    again = settings.view(owner, brand)
    assert again is not None and again.languages == ("en", "hi")
    assert settings.save(owner, brand, again.knobs) is Saved.UNCHANGED
    assert settings.save(owner, brand, replace(again.knobs, languages=("ta",))) is Saved.SAVED
    assert own_languages(committing_engine, brand) == {"ta"}  # exactly these: hi and en gone
    assert settings.save(owner, brand, replace(again.knobs, languages=())) is Saved.INVALID
    bad = replace(again.knobs, languages=("EN",))  # the form lowercases; the service checks
    assert settings.save(owner, brand, bad) is Saved.INVALID
    assert own_languages(committing_engine, brand) == {"ta"}


def own_languages(engine: Engine, brand_id: uuid.UUID) -> set[str]:
    rows = table("brand_languages")
    with engine.connect() as conn:
        query = select(rows.c.language_code).where(rows.c.brand_id == brand_id)
        return set(conn.execute(query).scalars())
