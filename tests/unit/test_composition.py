import uuid
from datetime import timedelta

import pytest

from serpsense.adapters.llm.profiles import PresetProfiles
from serpsense.adapters.mail.console import ConsoleMailer
from serpsense.adapters.mail.smtp import SmtpMailer
from serpsense.composition import (
    COLLECTION_TIME,
    SCAN_TIME_LIMIT_SECONDS,
    STUCK_AFTER,
    build_celery,
    build_container,
    build_evaluator,
    build_outbox,
    build_seeder,
    build_session_guard,
    build_sign_in,
    build_worker,
)
from serpsense.config import ConfigError, Settings
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import LlmPreset, preset_settings
from serpsense.services.dispatch import Dispatcher
from serpsense.services.scan_now import ScanNow
from serpsense.services.scans import ScanService
from serpsense.services.sweep import Sweeper
from tests.factories import make_settings

pytestmark = pytest.mark.unit


def test_build_container_wires_postgres_and_redis_checks_without_connecting(
    settings: Settings,
) -> None:
    container = build_container(settings)
    assert container.settings is settings
    assert [check.name for check in container.health_checks] == ["postgres", "redis"]
    assert isinstance(container.scan_now, ScanNow)


def test_build_celery_uses_redis_broker(settings: Settings) -> None:
    celery = build_celery(settings)
    assert celery.conf.broker_url == settings.redis_url.get_secret_value()


def test_build_worker_wires_the_scan_pipeline_without_connecting() -> None:
    settings = make_settings(serpapi_api_key="test-serp-key", anthropic_api_key="test-llm-key")
    worker = build_worker(settings, build_celery(settings))
    assert isinstance(worker.scans, ScanService)
    assert isinstance(worker.dispatcher, Dispatcher) and isinstance(worker.sweeper, Sweeper)
    assert COLLECTION_TIME < timedelta(seconds=SCAN_TIME_LIMIT_SECONDS) < STUCK_AFTER


@pytest.mark.parametrize("missing", ["serpapi_api_key", "anthropic_api_key"])
def test_a_worker_needs_both_api_keys(missing: str) -> None:
    keys = {"serpapi_api_key": "test-serp-key", "anthropic_api_key": "test-llm-key"}
    settings = make_settings(**{**keys, missing: None})
    with pytest.raises(ConfigError, match="needed to run scans"):
        build_worker(settings, build_celery(settings))


def test_until_profiles_can_be_edited_every_user_gets_the_configured_preset() -> None:
    profiles = PresetProfiles(LlmPreset.HIGH_THINKING)
    settings = profiles.settings(uuid.uuid4(), LlmTask.LABEL_MENTIONS)
    assert settings == preset_settings(LlmPreset.HIGH_THINKING, LlmTask.LABEL_MENTIONS)


def test_build_seeder_is_ready_without_connecting(settings: Settings) -> None:
    assert callable(build_seeder(settings, build_celery(settings)))


def test_build_outbox_sends_through_the_configured_mailer_without_connecting() -> None:
    smtp = make_settings(smtp_host="smtp.example.com", smtp_user="u", smtp_password="pw")
    dispatcher = build_outbox(smtp, build_celery(smtp))
    mailer = dispatcher._mailer  # the wiring is what this test checks
    assert isinstance(mailer, SmtpMailer)
    assert (mailer._settings.host, mailer._settings.password) == ("smtp.example.com", "pw")
    console = make_settings(email_backend="console")
    assert isinstance(build_outbox(console, build_celery(console))._mailer, ConsoleMailer)


def test_build_sign_in_wires_sign_in_without_connecting() -> None:
    settings = make_settings(signup_mode="invite", allowed_domains="serpsense.in")
    sign_in = build_sign_in(settings, build_celery(settings))
    assert sign_in._policy.allows("a@serpsense.in") and not sign_in._policy.allows("a@x.in")
    assert sign_in._keys.otp != sign_in._keys.csrf  # one key per purpose


def test_build_session_guard_wires_sessions_without_connecting() -> None:
    settings = make_settings()
    guard = build_session_guard(settings, build_celery(settings))
    assert guard._csrf_key != build_sign_in(settings, build_celery(settings))._keys.otp


def test_build_evaluator_needs_the_anthropic_key_and_says_which_model() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_evaluator(make_settings())
    _, model = build_evaluator(make_settings(anthropic_api_key="sk-ant-test-only"))
    assert model == "claude-opus-5-5"
