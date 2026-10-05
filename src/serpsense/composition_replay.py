"""The composition root's replay half (`SERPSENSE_MODE=replay`): what plays the recordings the
package ships into a database, and what records new ones. Kept apart from `composition` so each
stays within its size budget; it builds on the scan service composition wires for any mode."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from celery import Celery

from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.replay_export import SqlRecordingExport
from serpsense.adapters.db.stories import SqlStories
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.adapters.jobs.celery_queue import CeleryJobQueue
from serpsense.adapters.replay.clock import ReplayClock
from serpsense.adapters.replay.story.build import BRANDS as STORY
from serpsense.adapters.replay.story.build import recordings as story_recordings
from serpsense.adapters.system_clock import SystemClock
from serpsense.composition import build_drafter, build_scans
from serpsense.config import ConfigError, RunMode, Settings
from serpsense.domain.schedule import Schedule
from serpsense.services.demo import DemoBrand, Seeded, draft_a_reply, seed_demo
from serpsense.services.drafts import Outcome
from serpsense.services.replay import Played, RecordedScan, ReplayLoader

__all__ = ["Replayed", "build_recording_export", "build_replayer"]

HOUR = timedelta(hours=1)  # replay seeds the demo this long before its first recorded scan
# The demo story's brands (adapters/replay/story), as seed_demo seeds them: main brand first.
STORY_BRANDS = tuple(
    DemoBrand(brand.name, brand.slug, brand.app, Schedule(720 * brand.every), brand.settings)
    for brand in STORY
)


@dataclass(frozen=True)
class Replayed:
    seeded: Seeded
    played: Counter[Played]
    drafted: Outcome | None = None  # the demo's holding statement, while the owner has none


def build_replayer(settings: Settings, celery: Celery) -> Callable[[str], Replayed]:
    """Seeds the demo story's brands for replay mode, plays the story into them and, while the
    owner has no draft, drafts a holding statement for its largest story, under the owner with
    an email; needs SERPSENSE_MODE=replay and no API key."""
    if settings.serpsense_mode is not RunMode.REPLAY:
        raise ConfigError("recordings are played only with SERPSENSE_MODE=replay")
    engine, clock = create_db_engine(settings.database_url.get_secret_value()), ReplayClock()
    queue = CeleryJobQueue(celery)  # nudges the outbox worker after each scan's commit

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine, queue)

    loader = ReplayLoader(unit_of_work, build_scans(settings, engine, unit_of_work, clock), clock)
    recordings = [r for r in story_recordings() if r.scans]
    plan = [RecordedScan(r.brand, s.recorded_at, s.settings) for r in recordings for s in r.scans]
    recorded = {r.brand: r.scans[-1].settings for r in recordings}  # what Scan now asks
    drafter, stories = build_drafter(settings, engine, celery), SqlStories(engine.connect)

    def replay(owner_email: str) -> Replayed:
        clock.set(min((s.recorded_at for s in plan), default=SystemClock().now()) - HOUR)
        try:
            seeded = seed_demo(
                unit_of_work, clock, owner_email=owner_email, replay=recorded, brands=STORY_BRANDS
            )
            ids = (seeded.brand_id, *seeded.competitor_ids)
            brands = {demo.slug: brand for demo, brand in zip(STORY_BRANDS, ids, strict=True)}
            played = loader.load(seeded.owner_id, brands, plan)
            drafted = draft_a_reply(stories, drafter, seeded)
            return Replayed(seeded, played, None if drafted is None else drafted.outcome)
        finally:
            engine.dispose()  # a one-shot command: close its connections when done

    return replay


def build_recording_export(settings: Settings) -> SqlRecordingExport:
    """Records a brand's stored scans for replay mode; read-only, and needs no API key."""
    return SqlRecordingExport(create_db_engine(settings.database_url.get_secret_value()))
