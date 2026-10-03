"""Composition root: the only place that wires adapters into services (AGENTS.md §2)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from celery import Celery
from sqlalchemy import Engine

from serpsense.adapters.cache.health import RedisHealthCheck
from serpsense.adapters.cache.response_cache import RedisResponseCache
from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.health import PostgresHealthCheck
from serpsense.adapters.db.llm_ledger import SqlLlmLedger
from serpsense.adapters.db.search_ledger import SqlSearchLedger
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.adapters.jobs.celery_factory import (
    DISPATCH_TASK,
    HEARTBEAT_TASK,
    RUN_SCAN_TASK,
    SCAN_TIME_LIMIT_SECONDS,
    SWEEP_TASK,
    create_celery,
)
from serpsense.adapters.jobs.celery_queue import CeleryJobQueue
from serpsense.adapters.llm.anthropic_client import AnthropicClient
from serpsense.adapters.llm.profiles import PresetProfiles
from serpsense.adapters.llm.prompts import PromptLibrary
from serpsense.adapters.serp.client import SerpApiSearchProvider
from serpsense.adapters.serp.collectors import COLLECTORS
from serpsense.adapters.system_clock import SystemClock
from serpsense.config import AppEnv, ConfigError, Settings
from serpsense.observability import configure_logging
from serpsense.ports.clock import Clock
from serpsense.ports.health import HealthCheck
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.collection import CollectorRunner
from serpsense.services.demo import Seeded, seed_demo
from serpsense.services.dispatch import Dispatcher
from serpsense.services.labelling import Labeller
from serpsense.services.llm_gateway import LlmGateway
from serpsense.services.scans import ScanLimits, ScanPorts, ScanService
from serpsense.services.scoring_run import score_backlog
from serpsense.services.search import SearchLimits, SearchPorts, SearchService
from serpsense.services.sweep import Sweeper

__all__ = [
    "DISPATCH_TASK",
    "HEARTBEAT_TASK",
    "RUN_SCAN_TASK",
    "SCAN_TIME_LIMIT_SECONDS",
    "SWEEP_TASK",
    "Container",
    "Worker",
    "build_celery",
    "build_container",
    "build_seeder",
    "build_settings",
    "build_worker",
]

# Collection must end in time for labelling and the finish within the task's hard limit, and the
# sweep must not time out a scan its worker is still allowed to run.
COLLECTION_TIME = timedelta(seconds=SCAN_TIME_LIMIT_SECONDS) * 2 / 3
STUCK_AFTER = timedelta(seconds=SCAN_TIME_LIMIT_SECONDS) + timedelta(minutes=5)


@dataclass(frozen=True)
class Container:
    settings: Settings
    health_checks: tuple[HealthCheck, ...]


def build_settings(settings: Settings | None = None) -> Settings:
    """Load settings and configure logging; no connections are created."""
    resolved = settings if settings is not None else Settings()
    configure_logging(
        level=resolved.log_level,
        json=resolved.app_env is not AppEnv.DEVELOPMENT,
        known_secrets=resolved.secret_values(),
    )
    return resolved


def build_container(settings: Settings | None = None) -> Container:
    resolved = build_settings(settings)
    engine = create_db_engine(resolved.database_url.get_secret_value())
    return Container(
        settings=resolved,
        health_checks=(
            PostgresHealthCheck(engine),
            RedisHealthCheck(resolved.redis_url.get_secret_value()),
        ),
    )


def build_celery(settings: Settings) -> Celery:
    return create_celery(settings.redis_url.get_secret_value())


@dataclass(frozen=True)
class Worker:
    """What the scan and maintenance tasks run, built once per worker process."""

    scans: ScanService
    dispatcher: Dispatcher
    sweeper: Sweeper


def build_worker(settings: Settings, celery: Celery) -> Worker:
    """Creates the engine and clients; nothing connects until a task runs. Raises ConfigError
    without the SerpApi and Anthropic keys, which every scan needs."""
    serpapi_key, anthropic_key = settings.serpapi_api_key, settings.anthropic_api_key
    if serpapi_key is None or anthropic_key is None:
        raise ConfigError("SERPAPI_API_KEY and ANTHROPIC_API_KEY are needed to run scans")
    engine = create_db_engine(settings.database_url.get_secret_value())
    clock: Clock = SystemClock()
    queue = CeleryJobQueue(celery)

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine, queue)

    search = _search(settings, engine, serpapi_key.get_secret_value(), clock)
    gateway = LlmGateway(
        AnthropicClient(anthropic_key.get_secret_value(), PromptLibrary()),
        SqlLlmLedger(engine.begin),
        clock,
        monthly_budget_micros=lambda user_id: settings.default_monthly_llm_budget_micros,
    )
    ports = ScanPorts(
        unit_of_work,
        CollectorRunner(COLLECTORS, search, clock),
        Labeller(unit_of_work, gateway, clock),
        SqlSearchLedger(engine.begin),
        PresetProfiles(settings.default_llm_preset),
        clock,
    )
    limits = ScanLimits(COLLECTION_TIME, settings.default_monthly_search_budget)
    return Worker(ScanService(ports, limits), *_maintenance(settings, unit_of_work, clock))


def _search(settings: Settings, engine: Engine, api_key: str, clock: Clock) -> SearchService:
    ports = SearchPorts(
        SerpApiSearchProvider(api_key=api_key, clock=clock),
        SqlSearchLedger(engine.begin),
        RedisResponseCache(settings.redis_url.get_secret_value()),
        clock,
    )
    limits = SearchLimits(
        monthly_default=settings.default_monthly_search_budget,
        global_daily=settings.serpapi_daily_global_cap,
        per_scan=settings.max_searches_per_scan,
    )
    return SearchService(ports, limits)


def _maintenance(
    settings: Settings, unit_of_work: UnitOfWorkFactory, clock: Clock
) -> tuple[Dispatcher, Sweeper]:
    dispatcher = Dispatcher(
        unit_of_work, clock, max_searches_per_scan=settings.max_searches_per_scan
    )
    return dispatcher, Sweeper(unit_of_work, clock, stuck_after=STUCK_AFTER)


def build_scorer(settings: Settings, celery: Celery) -> Callable[[], int]:
    """Scores the finished scans that have no scores; the engine lasts as long as the command."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    queue = CeleryJobQueue(celery)
    return lambda: score_backlog(lambda: SqlUnitOfWork(engine, queue), SystemClock())


def build_seeder(settings: Settings, celery: Celery) -> Callable[[str], Seeded]:
    """Seeds the demo under the owner with an email; the engine lasts as long as the command."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    queue = CeleryJobQueue(celery)

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine, queue)

    def seed(owner_email: str) -> Seeded:
        return seed_demo(unit_of_work, SystemClock(), owner_email=owner_email)

    return seed
