"""Composition root: the only place that wires adapters into services (AGENTS.md §2)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from celery import Celery
from sqlalchemy import Engine

from serpsense.adapters.cache.health import RedisHealthCheck
from serpsense.adapters.cache.rate_limiter import RedisRateLimiter
from serpsense.adapters.cache.response_cache import RedisResponseCache
from serpsense.adapters.crypto.fernet_box import FernetBox
from serpsense.adapters.crypto.keys import KeyPurpose, derive_key
from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.health import PostgresHealthCheck
from serpsense.adapters.db.inbox import SqlInbox
from serpsense.adapters.db.llm_ledger import SqlLlmLedger
from serpsense.adapters.db.llm_profiles import SqlLlmProfiles
from serpsense.adapters.db.overview import SqlOverview
from serpsense.adapters.db.search_ledger import SqlSearchLedger
from serpsense.adapters.db.stories import SqlStories
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
from serpsense.adapters.llm.prompts import PromptLibrary
from serpsense.adapters.mail.console import ConsoleMailer
from serpsense.adapters.mail.smtp import SmtpMailer, SmtpSettings
from serpsense.adapters.serp.client import SerpApiSearchProvider
from serpsense.adapters.serp.collectors import COLLECTORS
from serpsense.adapters.system_clock import SystemClock
from serpsense.config import AppEnv, ConfigError, EmailBackend, Settings, SignupMode
from serpsense.domain.auth import SignupPolicy
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import preset_settings
from serpsense.observability import configure_logging
from serpsense.ports.clock import Clock
from serpsense.ports.health import HealthCheck
from serpsense.ports.inbox import Inbox
from serpsense.ports.mailer import Mailer
from serpsense.ports.overview import Overview
from serpsense.ports.stories import Stories
from serpsense.ports.unit_of_work import UnitOfWorkFactory
from serpsense.services.accounts import AccountDeletion
from serpsense.services.auth import AuthKeys, SignIn, SignInPorts
from serpsense.services.brand_settings import BrandSettings
from serpsense.services.collection import CollectorRunner
from serpsense.services.demo import Seeded, seed_demo
from serpsense.services.dispatch import Dispatcher
from serpsense.services.evals import Evaluator, MemoryLedger
from serpsense.services.grouping import Grouper
from serpsense.services.grouping_eval import GroupingEvaluator
from serpsense.services.labelling import Labeller
from serpsense.services.llm_gateway import LlmGateway
from serpsense.services.outbox import OutboxDispatcher
from serpsense.services.scan_now import ScanNow, ScanNowLimits
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
    "build_account_deletion",
    "build_brand_settings",
    "build_celery",
    "build_container",
    "build_evaluator",
    "build_grouping_evaluator",
    "build_outbox",
    "build_scan_now",
    "build_seeder",
    "build_session_guard",
    "build_settings",
    "build_sign_in",
    "build_worker",
]

# Collection must end in time for labelling and the finish within the task's hard limit, and the
# sweep must not time out a scan its worker is still allowed to run. Grouping starts no batch
# after ENRICH_TIME, leaving a batch's model call (a minute) and the finish inside the limit.
COLLECTION_TIME = timedelta(seconds=SCAN_TIME_LIMIT_SECONDS) * 2 / 3
ENRICH_TIME = timedelta(seconds=SCAN_TIME_LIMIT_SECONDS) - timedelta(minutes=3)
STUCK_AFTER = timedelta(seconds=SCAN_TIME_LIMIT_SECONDS) + timedelta(minutes=5)


@dataclass(frozen=True, kw_only=True)
class Container:
    """What the web app's routes use, built once per process."""

    settings: Settings
    health_checks: tuple[HealthCheck, ...]
    sign_in: SignIn
    sessions: SessionGuard
    overview: Overview
    scan_now: ScanNow
    inbox: Inbox
    brand_settings: BrandSettings
    stories: Stories
    accounts: AccountDeletion


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
    celery = build_celery(resolved)
    sign_in = build_sign_in(resolved, celery)
    return Container(
        settings=resolved,
        health_checks=(
            PostgresHealthCheck(engine),
            RedisHealthCheck(resolved.redis_url.get_secret_value()),
        ),
        sign_in=sign_in,
        sessions=build_session_guard(resolved, celery),
        overview=SqlOverview(engine.connect),
        scan_now=build_scan_now(resolved, engine, celery),
        inbox=SqlInbox(engine, SystemClock()),
        brand_settings=build_brand_settings(resolved, engine, celery),
        stories=SqlStories(engine.connect),
        accounts=build_account_deletion(resolved, celery, sign_in),
    )


def build_brand_settings(settings: Settings, engine: Engine, celery: Celery) -> BrandSettings:
    """A brand's search settings page; saving sends no jobs."""
    queue = CeleryJobQueue(celery)
    return BrandSettings(
        lambda: SqlUnitOfWork(engine, queue),
        SystemClock(),
        max_searches_per_scan=settings.max_searches_per_scan,
    )


def build_scan_now(settings: Settings, engine: Engine, celery: Celery) -> ScanNow:
    """ "Scan now" for the web app; its scan's job is sent after the commit."""
    queue = CeleryJobQueue(celery)
    limits = ScanNowLimits(settings.max_searches_per_scan, settings.default_monthly_search_budget)
    return ScanNow(
        lambda: SqlUnitOfWork(engine, queue),
        SqlSearchLedger(engine.begin),
        SystemClock(),
        limits,
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
        unit_of_work=unit_of_work,
        collector=CollectorRunner(COLLECTORS, search, clock),
        labeller=Labeller(unit_of_work, gateway, clock),
        grouper=Grouper(unit_of_work, gateway, clock),
        usage=SqlSearchLedger(engine.begin),
        profiles=SqlLlmProfiles(engine, settings.default_llm_preset),
        clock=clock,
    )
    limits = ScanLimits(
        time_limit=COLLECTION_TIME,
        enrich_time=ENRICH_TIME,
        monthly_searches=settings.default_monthly_search_budget,
    )
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


def build_account_deletion(settings: Settings, celery: Celery, sign_in: SignIn) -> AccountDeletion:
    """Account deletion (ADR-0013), confirming with the sign-in service's codes."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    queue = CeleryJobQueue(celery)
    return AccountDeletion(lambda: SqlUnitOfWork(engine, queue), sign_in, SystemClock())


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
    gateway, ledger = _eval_gateway(settings)
    task_settings = preset_settings(settings.default_llm_preset, LlmTask.LABEL_MENTIONS)
    return Evaluator(gateway, ledger, task_settings), task_settings.model


def build_grouping_evaluator(settings: Settings) -> tuple[GroupingEvaluator, str]:
    """The same for narrative grouping, on the preset's grouping settings."""
    gateway, ledger = _eval_gateway(settings)
    task_settings = preset_settings(settings.default_llm_preset, LlmTask.GROUP_NARRATIVES)
    return GroupingEvaluator(gateway, ledger, task_settings), task_settings.model


def _eval_gateway(settings: Settings) -> tuple[LlmGateway, MemoryLedger]:
    key = settings.anthropic_api_key
    if key is None:
        raise ConfigError("ANTHROPIC_API_KEY is needed to run an eval")
    ledger = MemoryLedger()
    client = AnthropicClient(key.get_secret_value(), PromptLibrary())
    gateway = LlmGateway(client, ledger, SystemClock(), monthly_budget_micros=lambda _: 2**62)
    return gateway, ledger


def build_seeder(settings: Settings, celery: Celery) -> Callable[[str], Seeded]:
    """Seeds the demo under the owner with an email; the engine lasts as long as the command."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    queue = CeleryJobQueue(celery)

    def unit_of_work() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine, queue)

    def seed(owner_email: str) -> Seeded:
        return seed_demo(unit_of_work, SystemClock(), owner_email=owner_email)

    return seed
