"""Adding a brand (BUILD_PLAN §13 onboarding, kept small): its name, its Google Play app, up to
four competitors, a preset and a schedule, all in one unit of work.

Each competitor becomes a brand of the same owner on the Lean preset and the same schedule,
linked to the new brand, so it is scanned and compared like any other. Brands are keyed by
their slug, so adding a name the owner already has updates that brand's settings rather than
making a second one.
"""

import re
import uuid
from dataclasses import dataclass

from serpsense.domain.enums import AppStore
from serpsense.domain.schedule import Schedule
from serpsense.domain.settings.search import PRESETS, Preset, resolve
from serpsense.observability import get_logger
from serpsense.ports.brand_store import NewBrand
from serpsense.ports.clock import Clock
from serpsense.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

log = get_logger(__name__)

MAX_COMPETITORS = 4
PLAY_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")


class InvalidBrand(ValueError):
    """What the form asked for can't be a brand; the message says why, in plain words."""


@dataclass(frozen=True, kw_only=True)
class BrandRequest:
    name: str
    play_app: str | None
    competitors: tuple[str, ...]
    preset: Preset
    interval_minutes: int | None  # None: scanned on request only


class BrandCreator:
    def __init__(self, unit_of_work: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work, self._clock = unit_of_work, clock

    def add(self, owner_id: uuid.UUID, request: BrandRequest) -> uuid.UUID:
        """The new brand's id; raises InvalidBrand for a request that can't be one."""
        name, rivals = _checked(request)
        schedule = None if request.interval_minutes is None else Schedule(request.interval_minutes)
        with self._unit_of_work() as uow:
            brand_id = self._brand(uow, owner_id, name, request.preset, schedule)
            if request.play_app:
                uow.brands.add_app(brand_id, AppStore.GOOGLE_PLAY, request.play_app)
            for rival in rivals:
                rival_id = self._brand(uow, owner_id, rival, Preset.LEAN, schedule)
                uow.brands.link_competitor(brand_id, rival_id)
        log.info("brand.added", user_id=str(owner_id), brand_id=str(brand_id), rivals=len(rivals))
        return brand_id

    def _brand(
        self,
        uow: UnitOfWork,
        owner_id: uuid.UUID,
        name: str,
        preset: Preset,
        schedule: Schedule | None,
    ) -> uuid.UUID:
        now = self._clock.now()
        brand_id = uow.brands.brand(NewBrand(owner_id, name, slug(name), now))
        settings = dict(PRESETS[preset])
        resolve(settings)  # a document a scan can use
        uow.brands.set_search_settings(brand_id, settings, at=now)
        uow.brands.set_schedule(brand_id, schedule, at=now)
        return brand_id


def slug(name: str) -> str:
    """Lowercase words joined by hyphens, as brands are keyed per owner."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    return "-".join(words)[:64].strip("-")


def _checked(request: BrandRequest) -> tuple[str, tuple[str, ...]]:
    name = " ".join(request.name.split())
    if not name or len(name) > 120 or not slug(name):
        raise InvalidBrand("A brand needs a name of up to 120 characters, with letters or digits.")
    if request.play_app and not PLAY_ID.fullmatch(request.play_app):
        raise InvalidBrand("A Google Play app id looks like com.example.app.")
    rivals = tuple(dict.fromkeys(" ".join(r.split()) for r in request.competitors if r.strip()))
    if len(rivals) > MAX_COMPETITORS:
        raise InvalidBrand("A brand can be compared with at most four competitors.")
    if any(slug(r) == slug(name) or not slug(r) or len(r) > 120 for r in rivals):
        raise InvalidBrand("Each competitor needs its own name, different from the brand's.")
    return name, rivals
