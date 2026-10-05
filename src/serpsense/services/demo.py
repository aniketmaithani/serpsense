"""The demo: Ola and its four competitors, set up as BUILD_PLAN §22 plans them.

The demo runs on SerpApi's free plan (250 searches a month), so it uses lighter settings than the
product defaults. Ola is scanned every 12 hours with at most 8 searches: one search page with its
AI Overview, one autocomplete prefix, English news, the Trends comparison with all four
competitors plus Ola's related queries, and its Play rating with a page of newest reviews. Each
competitor is scanned every 24 hours with 3: its search page, news and Play rating. Seeding again
changes nothing, unless the brands' settings or schedules were edited since: those are put back to
the demo's as new versions. Replay mode (`SERPSENSE_MODE=replay`) seeds the demo story's brands
instead (composition gives them), each with the settings its recordings were made with and
scanned only on request: the replay loader plays the recorded scans (services/replay.py), and
repeating them on a schedule would add nothing.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from serpsense.domain.enums import AppStore, DraftKind, DraftPreset
from serpsense.domain.schedule import Schedule
from serpsense.domain.settings.search import resolve
from serpsense.observability import get_logger
from serpsense.ports.brand_store import NewBrand
from serpsense.ports.clock import Clock
from serpsense.ports.stories import Stories
from serpsense.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from serpsense.services.drafts import Drafter, DraftResult

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
            # Plain "Ola" finds Ola Electric's showrooms, a sister company the labeller rightly
            # leaves out; "Ola cabs" finds the ride-hailing brand.
            "search_page": {"templates": ["{brand} cabs"], "pages": 1, "ai_overview": True},
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


DEMO_BRANDS = (OLA, *RIVALS)  # in the order seed_demo seeds them


@dataclass(frozen=True)
class Seeded:
    owner_id: uuid.UUID
    brand_id: uuid.UUID
    competitor_ids: tuple[uuid.UUID, ...]


def seed_demo(
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
    *,
    owner_email: str,
    replay: Mapping[str, Mapping[str, Any]] | None = None,
    brands: Sequence[DemoBrand] = DEMO_BRANDS,
) -> Seeded:
    """The demo's main brand and its competitors (`brands`, main first; Ola's by default)
    under the owner with this email (created if new), in one unit of work. For replay mode,
    `replay` gives each brand's recorded settings by slug, and no brand is scheduled."""
    with unit_of_work() as uow:
        owner = uow.accounts.user_for(owner_email, at=clock.now())
        main, *rest = (_brand(uow, owner, demo, clock, replay) for demo in brands)
        rivals = tuple(rest)
        for rival in rivals:
            uow.brands.link_competitor(main, rival)
    log.info("demo.seeded", user_id=str(owner), brand_id=str(main))
    return Seeded(owner, main, rivals)


def _brand(
    uow: UnitOfWork,
    owner: uuid.UUID,
    demo: DemoBrand,
    clock: Clock,
    replay: Mapping[str, Mapping[str, Any]] | None,
) -> uuid.UUID:
    now = clock.now()
    brand_id = uow.brands.brand(NewBrand(owner, demo.name, demo.slug, now))
    for alias in demo.aliases:
        uow.brands.add_alias(brand_id, alias)
    uow.brands.add_app(brand_id, AppStore.GOOGLE_PLAY, demo.app)
    uow.brands.set_schedule(brand_id, None if replay is not None else demo.every, at=now)
    settings = (replay or {}).get(demo.slug, demo.settings)
    resolve(settings)  # a document the dispatcher can use, or ValidationError
    uow.brands.set_search_settings(brand_id, settings, at=now)
    return brand_id


def draft_a_reply(stories: Stories, drafter: Drafter, seeded: Seeded) -> DraftResult | None:
    """A holding statement for the main brand's largest story, as its owner would ask for one,
    so the demo's Drafts page has a draft; None when the brand has no story or the owner has a
    draft already, so seeding again adds none and a failed draft is tried again."""
    largest = stories.of_brand(seeded.owner_id, seeded.brand_id, limit=1) or []
    if not largest or drafter.recent(seeded.owner_id, limit=1):
        return None
    story = largest[0].narrative_id
    return drafter.draft(
        seeded.owner_id,
        seeded.brand_id,
        story,
        kind=DraftKind.HOLDING_STATEMENT,
        preset=DraftPreset.STANDARD,
    )
