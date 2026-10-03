"""The demo: Ola and its four competitors, set up as BUILD_PLAN §22 plans them.

The demo runs on SerpApi's free plan (250 searches a month), so it uses lighter settings than the
product defaults. Ola is scanned every 12 hours with at most 8 searches: one search page with its
AI Overview, one autocomplete prefix, English news, the Trends comparison with all four
competitors plus Ola's related queries, and its Play rating with a page of newest reviews. Each
competitor is scanned every 24 hours with 3: its search page, news and Play rating. Seeding again
changes nothing, unless the brands' settings or schedules were edited since: those are put back to
the demo's as new versions.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from serpsense.domain.enums import AppStore
from serpsense.domain.schedule import Schedule
from serpsense.domain.settings.search import resolve
from serpsense.observability import get_logger
from serpsense.ports.brand_store import NewBrand
from serpsense.ports.clock import Clock
from serpsense.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

log = get_logger(__name__)

OFF = MappingProxyType({"enabled": False})


@dataclass(frozen=True)
class DemoBrand:
    name: str
    slug: str
    app: str  # its Google Play id
    every: Schedule
    settings: Mapping[str, Any]  # a search settings document (domain/settings/search.py)
    aliases: tuple[str, ...] = ()


OLA = DemoBrand(
    "Ola",
    "ola",
    "com.olacabs.customer",
    Schedule(720),
    MappingProxyType(
        {
            "languages": ["en"],
            "search_page": {"templates": ["{brand}"], "pages": 1, "ai_overview": True},
            "autocomplete": {"prefixes": ["{brand} "]},
            "news": {"extra_terms": []},
            "trends": {"related_queries": True},
            "play": {"review_sort": "newest", "review_pages": 1},
            "maps": OFF,
            "youtube": OFF,
        }
    ),
    aliases=("Ola Cabs",),
)
RIVAL_SETTINGS = MappingProxyType(
    {
        "languages": ["en"],
        "search_page": {"templates": ["{brand}"], "pages": 1, "ai_overview": False},
        "autocomplete": OFF,
        "news": {"extra_terms": []},
        "trends": OFF,  # Ola's scan compares all five
        "play": {"review_pages": 0},  # the rating only
        "maps": OFF,
        "youtube": OFF,
    }
)
RIVALS = tuple(
    DemoBrand(name, slug, app, Schedule(1440), RIVAL_SETTINGS)
    for name, slug, app in (
        ("Uber", "uber", "com.ubercab"),
        ("Rapido", "rapido", "com.rapido.passenger"),
        ("Namma Yatri", "namma-yatri", "in.juspay.nammayatri"),
        ("inDrive", "indrive", "sinet.startup.inDriver"),
    )
)


@dataclass(frozen=True)
class Seeded:
    owner_id: uuid.UUID
    brand_id: uuid.UUID
    competitor_ids: tuple[uuid.UUID, ...]


def seed_demo(unit_of_work: UnitOfWorkFactory, clock: Clock, *, owner_email: str) -> Seeded:
    """Ola and its competitors under the owner with this email (created if new), in one unit
    of work."""
    with unit_of_work() as uow:
        owner = uow.accounts.user_for(owner_email, at=clock.now())
        ola = _brand(uow, owner, OLA, clock)
        rivals = tuple(_brand(uow, owner, rival, clock) for rival in RIVALS)
        for rival in rivals:
            uow.brands.link_competitor(ola, rival)
    log.info("demo.seeded", user_id=str(owner), brand_id=str(ola))
    return Seeded(owner, ola, rivals)


def _brand(uow: UnitOfWork, owner: uuid.UUID, demo: DemoBrand, clock: Clock) -> uuid.UUID:
    now = clock.now()
    brand_id = uow.brands.brand(NewBrand(owner, demo.name, demo.slug, now))
    for alias in demo.aliases:
        uow.brands.add_alias(brand_id, alias)
    uow.brands.add_app(brand_id, AppStore.GOOGLE_PLAY, demo.app)
    uow.brands.set_schedule(brand_id, demo.every, at=now)
    resolve(demo.settings)  # a document the dispatcher can use, or ValidationError
    uow.brands.set_search_settings(brand_id, demo.settings, at=now)
    return brand_id
