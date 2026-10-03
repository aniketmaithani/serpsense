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
from tests.integration.db_helpers import NOW, add_brand, add_user, table

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
