"""Composition root: the only place that wires adapters into services (AGENTS.md §2)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from celery import Celery
from sqlalchemy import Engine

from serpsense.adapters.cache.health import RedisHealthCheck
from serpsense.adapters.cache.null_cache import NullResponseCache
from serpsense.adapters.cache.rate_limiter import RedisRateLimiter
from serpsense.adapters.cache.response_cache import RedisResponseCache
from serpsense.adapters.crypto.fernet_box import FernetBox
from serpsense.adapters.crypto.keys import KeyPurpose, derive_key
from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.health import PostgresHealthCheck
from serpsense.adapters.db.llm_ledger import SqlLlmLedger
from serpsense.adapters.db.overview import SqlOverview
from serpsense.adapters.db.replay_export import SqlRecordingExport
from serpsense.adapters.db.search_ledger import SqlSearchLedger
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.adapters.jobs.celery_factory import (
    DISPATCH_TASK,
    HEARTBEAT_TASK,
    OUTBOX_TASK,
    RUN_SCAN_TASK,
    SCAN_TIME_LIMIT_SECONDS,
    SWEEP_TASK,
    create_celery,
)
from serpsense.adapters.jobs.celery_queue import CeleryJobQueue
from serpsense.adapters.llm.anthropic_client import AnthropicClient
from serpsense.adapters.llm.profiles import PresetProfiles
from serpsense.adapters.llm.prompts import PromptLibrary
from serpsense.adapters.llm.replay import ReplayLlm
from serpsense.adapters.mail.console import ConsoleMailer
from serpsense.adapters.mail.smtp import SmtpMailer, SmtpSettings
from serpsense.adapters.replay.recording import load as load_recordings
from serpsense.adapters.serp.client import SerpApiSearchProvider
from serpsense.adapters.serp.collectors import COLLECTORS
from serpsense.adapters.serp.replay import ReplaySearchProvider
from serpsense.adapters.system_clock import SystemClock
from serpsense.config import AppEnv, ConfigError, EmailBackend, RunMode, Settings, SignupMode
from serpsense.domain.auth import SignupPolicy
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import preset_settings
from serpsense.observability import configure_logging
from serpsense.ports.clock import Clock
from serpsense.ports.health import HealthCheck
from serpsense.ports.llm_client import LLMClient
from serpsense.ports.mailer import Mailer
from serpsense.ports.overview import Overview
from serpsense.ports.response_cache import ResponseCache
from serpsense.ports.search_provider import SearchProvider
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.auth import AuthKeys, SignIn, SignInPorts
from serpsense.services.collection import CollectorRunner
from serpsense.services.demo import Seeded, seed_demo
from serpsense.services.dispatch import Dispatcher
from serpsense.services.evals import Evaluator, MemoryLedger
from serpsense.services.labelling import Labeller
from serpsense.services.llm_gateway import LlmGateway
from serpsense.services.outbox import OutboxDispatcher
from serpsense.services.scans import ScanLimits, ScanPorts, ScanService
from serpsense.services.scoring_run import score_backlog
from serpsense.services.search import SearchLimits, SearchPorts, SearchService
from serpsense.services.sessions import SessionGuard
from serpsense.services.sweep import Sweeper

__all__ = [
    "DISPATCH_TASK",
    "HEARTBEAT_TASK",
    "OUTBOX_TASK",
    "RUN_SCAN_TASK",
    "SCAN_TIME_LIMIT_SECONDS",
    "SWEEP_TASK",
    "Container",
    "Worker",
    "build_celery",
    "build_container",
    "build_evaluator",
    "build_outbox",
    "build_recording_export",
    "build_seeder",
    "build_session_guard",
    "build_settings",
    "build_sign_in",
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
    sign_in: SignIn
    sessions: SessionGuard
    overview: Overview


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
        sign_in=build_sign_in(resolved, build_celery(resolved)),
        sessions=build_session_guard(resolved, build_celery(resolved)),
        overview=SqlOverview(engine.connect),
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
    """Creates the engine and clients; nothing connects until a task runs. Live mode raises
    ConfigError without the SerpApi and Anthropic keys, which every live scan needs; replay
    mode needs neither, answering from the recordings the package ships."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    clock: Clock = SystemClock()
    queue = CeleryJobQueue(celery)

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine, queue)

    sources = _sources(settings, engine, clock)
    gateway = LlmGateway(
        sources.llm,
        SqlLlmLedger(engine.begin),
        clock,
        monthly_budget_micros=lambda user_id: settings.default_monthly_llm_budget_micros,
    )
    ports = ScanPorts(
        unit_of_work,
        CollectorRunner(COLLECTORS, _search(settings, engine, sources, clock), clock),
        Labeller(unit_of_work, gateway, clock),
        SqlSearchLedger(engine.begin),
        PresetProfiles(settings.default_llm_preset),
        clock,
    )
    limits = ScanLimits(COLLECTION_TIME, settings.default_monthly_search_budget)
    return Worker(ScanService(ports, limits), *_maintenance(settings, unit_of_work, clock))


@dataclass(frozen=True)
class _Sources:
    """Where scans get their search answers and labels: SerpApi and Claude, or a recording."""

    search: SearchProvider
    cache: ResponseCache
    llm: LLMClient


def _sources(settings: Settings, engine: Engine, clock: Clock) -> _Sources:
    if settings.serpsense_mode is RunMode.REPLAY:
        recordings = load_recordings()
        answered = SqlSearchLedger(engine.begin).times_answered
        return _Sources(
            ReplaySearchProvider(recordings, answered), NullResponseCache(), ReplayLlm(recordings)
        )
    serpapi_key, anthropic_key = settings.serpapi_api_key, settings.anthropic_api_key
    if serpapi_key is None or anthropic_key is None:
        raise ConfigError("SERPAPI_API_KEY and ANTHROPIC_API_KEY are needed to run scans")
    return _Sources(
        SerpApiSearchProvider(api_key=serpapi_key.get_secret_value(), clock=clock),
        RedisResponseCache(settings.redis_url.get_secret_value()),
        AnthropicClient(anthropic_key.get_secret_value(), PromptLibrary()),
    )


def _search(settings: Settings, engine: Engine, sources: _Sources, clock: Clock) -> SearchService:
    ports = SearchPorts(sources.search, SqlSearchLedger(engine.begin), sources.cache, clock)
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


def build_outbox(settings: Settings, celery: Celery) -> OutboxDispatcher:
    """The outbox dispatcher (ADR-0010); it needs no SerpApi or Anthropic key."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    queue = CeleryJobQueue(celery)
    box = FernetBox(settings.outbox_key_list)
    return OutboxDispatcher(
        lambda: SqlUnitOfWork(engine, queue), _mailer(settings), box, SystemClock()
    )


def _mailer(settings: Settings) -> Mailer:
    if settings.email_backend is EmailBackend.CONSOLE:
        return ConsoleMailer(production=settings.is_production)
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else ""
    smtp = SmtpSettings(
        host=settings.smtp_host,
        port=settings.smtp_port,
        sender=settings.email_from,
        user=settings.smtp_user,
        password=password,
        starttls=settings.smtp_starttls,
    )
    return SmtpMailer(smtp)


def build_sign_in(settings: Settings, celery: Celery) -> SignIn:
    """Sign-in (ADR-0009): keys derived from SECRET_KEY per purpose, the invite policy from the
    settings; nothing connects until a request comes."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    queue = CeleryJobQueue(celery)
    secret = settings.secret_key.get_secret_value()
    limiter_key = derive_key(secret, KeyPurpose.RATE_LIMIT)
    ports = SignInPorts(
        lambda: SqlUnitOfWork(engine, queue),
        FernetBox(settings.outbox_key_list),
        RedisRateLimiter(settings.redis_url.get_secret_value(), limiter_key),
        SystemClock(),
    )
    keys = AuthKeys(derive_key(secret, KeyPurpose.OTP), derive_key(secret, KeyPurpose.CSRF))
    policy = SignupPolicy(
        settings.signup_mode is SignupMode.INVITE,
        frozenset(settings.allowed_email_list),
        frozenset(settings.allowed_domain_list),
    )
    return SignIn(ports, keys, policy, session_days=settings.session_days)


def build_session_guard(settings: Settings, celery: Celery) -> SessionGuard:
    """Signed-in sessions (ADR-0009), with the CSRF key derived from SECRET_KEY."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    queue = CeleryJobQueue(celery)
    csrf = derive_key(settings.secret_key.get_secret_value(), KeyPurpose.CSRF)
    return SessionGuard(
        lambda: SqlUnitOfWork(engine, queue),
        SystemClock(),
        csrf,
        session_days=settings.session_days,
    )


def build_evaluator(settings: Settings) -> tuple[Evaluator, str]:
    """An evaluator on the real model and the configured preset, and the model it asks
    (AGENTS §7); its ledger is in memory, so a run stores nothing."""
    key = settings.anthropic_api_key
    if key is None:
        raise ConfigError("ANTHROPIC_API_KEY is needed to run an eval")
    ledger = MemoryLedger()
    client = AnthropicClient(key.get_secret_value(), PromptLibrary())
    gateway = LlmGateway(client, ledger, SystemClock(), monthly_budget_micros=lambda _: 2**62)
    task_settings = preset_settings(settings.default_llm_preset, LlmTask.LABEL_MENTIONS)
    return Evaluator(gateway, ledger, task_settings), task_settings.model


def build_recording_export(settings: Settings) -> SqlRecordingExport:
    """Records a brand's stored scans for replay mode; read-only, and needs no API key."""
    return SqlRecordingExport(create_db_engine(settings.database_url.get_secret_value()))


def build_seeder(settings: Settings, celery: Celery) -> Callable[[str], Seeded]:
    """Seeds the demo under the owner with an email; the engine lasts as long as the command."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    queue = CeleryJobQueue(celery)

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine, queue)

    def seed(owner_email: str) -> Seeded:
        replay = settings.serpsense_mode is RunMode.REPLAY
        return seed_demo(unit_of_work, SystemClock(), owner_email=owner_email, replay=replay)

    return seed
