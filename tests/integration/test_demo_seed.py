"""Seeding the demo on real Postgres, and the dispatcher taking it from there."""

import uuid

import pytest
from sqlalchemy import Engine, func, select

from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.services.demo import seed_demo
from serpsense.services.dispatch import Dispatcher
from tests.fakes import FixedClock
from tests.integration.db_helpers import NOW, table

pytestmark = pytest.mark.integration


class Queue:
    def __init__(self) -> None:
        self.sent: list[uuid.UUID] = []

    def run_scan(self, scan_id: uuid.UUID) -> None:
        self.sent.append(scan_id)


def test_the_demo_is_seeded_once_and_scheduled_by_the_dispatcher(committing_engine: Engine) -> None:
    queue = Queue()

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(committing_engine, queue)

    email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
    seeded = seed_demo(unit_of_work, FixedClock(NOW), owner_email=email)
    again = seed_demo(unit_of_work, FixedClock(NOW), owner_email=email.upper())
    assert again == seeded  # the same owner and brands; nothing written twice
    ours = (seeded.brand_id, *seeded.competitor_ids)
    with committing_engine.connect() as conn:
        for name in ("brand_apps", "brand_schedule_versions", "brand_search_settings_versions"):
            rows = table(name)
            written = select(func.count()).where(rows.c.brand_id.in_(ours))
            assert conn.execute(written).scalar_one() == 5, name
        links = table("brand_competitors")
        linked = select(links.c.competitor_brand_id).where(links.c.brand_id == seeded.brand_id)
        assert set(conn.execute(linked).scalars()) == set(seeded.competitor_ids)

    Dispatcher(unit_of_work, FixedClock(NOW), max_searches_per_scan=40).dispatch()
    scans = table("scans")
    with committing_engine.connect() as conn:
        queued = select(scans.c.brand_id, scans.c.estimated_searches).where(
            scans.c.brand_id.in_(ours)
        )
        estimates = dict(conn.execute(queued).all())
    assert estimates == {seeded.brand_id: 8} | {rival: 3 for rival in seeded.competitor_ids}
