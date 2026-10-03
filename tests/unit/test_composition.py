from datetime import timedelta

import pytest

from serpsense.adapters.cache.null_cache import NullResponseCache
from serpsense.adapters.db.llm_profiles import SqlLlmProfiles
from serpsense.adapters.db.replay_export import SqlRecordingExport
from serpsense.adapters.llm.replay import ReplayLlm
from serpsense.adapters.llm.unavailable import UnavailableClient
from serpsense.adapters.mail.console import ConsoleMailer
from serpsense.adapters.mail.smtp import SmtpMailer
from serpsense.adapters.serp.replay import ReplaySearchProvider
from serpsense.composition import (
    COLLECTION_TIME,
    SCAN_TIME_LIMIT_SECONDS,
    STUCK_AFTER,
    build_account_deletion,
    build_celery,
    build_container,
    build_evaluator,
    build_grouping_evaluator,
    build_outbox,
    build_output_evaluator,
    build_seeder,
    build_session_guard,
    build_sign_in,
    build_worker,
)
from serpsense.composition_replay import build_recording_export, build_replayer
from serpsense.config import ConfigError, Settings
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import LlmPreset, preset_settings, request_shape
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.services.dispatch import Dispatcher
from serpsense.services.drafts import Drafter
from serpsense.services.explanations import Explainer
from serpsense.services.output_evals import OutputEvaluator
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
    assert isinstance(container.drafts, Drafter)


def test_without_an_anthropic_key_the_web_drafter_fails_at_once_offline() -> None:
    request = LlmRequest(
        task=LlmTask.DRAFT_RESPONSE,
        prompt_version="draft_response/v1",
        variables={},
        shape=request_shape(preset_settings(LlmPreset.BALANCED, LlmTask.DRAFT_RESPONSE)),
        output_schema={},
    )
    with pytest.raises(LlmCallFailed) as failed:
        UnavailableClient().complete(request)
    assert (failed.value.code, failed.value.retryable) == ("llm.unconfigured", False)


def test_build_celery_uses_redis_broker(settings: Settings) -> None:
    celery = build_celery(settings)
    assert celery.conf.broker_url == settings.redis_url.get_secret_value()


def test_build_worker_wires_the_scan_pipeline_without_connecting() -> None:
    settings = make_settings(serpapi_api_key="test-serp-key", anthropic_api_key="test-llm-key")
    worker = build_worker(settings, build_celery(settings))
    assert isinstance(worker.scans, ScanService)
    assert isinstance(worker.dispatcher, Dispatcher) and isinstance(worker.sweeper, Sweeper)
    assert isinstance(worker.explainer, Explainer)
    assert COLLECTION_TIME < timedelta(seconds=SCAN_TIME_LIMIT_SECONDS) < STUCK_AFTER


def test_a_worker_reads_each_users_saved_model_settings() -> None:
    keys = {"serpapi_api_key": "test-serp-key", "anthropic_api_key": "test-llm-key"}
    settings = make_settings(**keys)
    worker = build_worker(settings, build_celery(settings))
    assert isinstance(worker.scans._ports.profiles, SqlLlmProfiles)


@pytest.mark.parametrize("missing", ["serpapi_api_key", "anthropic_api_key"])
def test_a_worker_needs_both_api_keys(missing: str) -> None:
    keys = {"serpapi_api_key": "test-serp-key", "anthropic_api_key": "test-llm-key"}
    settings = make_settings(**{**keys, missing: None})
    with pytest.raises(ConfigError, match="needed to run scans"):
        build_worker(settings, build_celery(settings))


def test_a_replay_worker_needs_no_api_keys_and_answers_from_recordings() -> None:
    settings = make_settings(serpsense_mode="replay")
    worker = build_worker(settings, build_celery(settings))
    search = worker.scans._ports.collector._searcher._ports  # type: ignore[attr-defined]  # wiring
    assert isinstance(search.provider, ReplaySearchProvider)
    assert isinstance(search.cache, NullResponseCache)
    assert isinstance(worker.scans._ports.labeller._gateway._client, ReplayLlm)


def test_recordings_are_played_only_in_replay_mode_and_need_no_api_key() -> None:
    live = make_settings()
    with pytest.raises(ConfigError, match="SERPSENSE_MODE=replay"):
        build_replayer(live, build_celery(live))
    replay = make_settings(serpsense_mode="replay")
    assert callable(build_replayer(replay, build_celery(replay)))


def test_build_recording_export_needs_no_api_key_and_connects_to_nothing() -> None:
    assert isinstance(build_recording_export(make_settings()), SqlRecordingExport)


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
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_grouping_evaluator(make_settings())
    settings = make_settings(anthropic_api_key="sk-ant-test-only")
    grouping, model = build_grouping_evaluator(settings)
    preset = preset_settings(settings.default_llm_preset, LlmTask.GROUP_NARRATIVES)
    assert (grouping.settings, model) == (preset, preset.model)


def test_build_account_deletion_confirms_with_the_sign_in_codes() -> None:
    settings = make_settings()
    sign_in = build_sign_in(settings, build_celery(settings))
    assert build_account_deletion(settings, build_celery(settings), sign_in)._sign_in is sign_in


def test_build_output_evaluator_needs_the_anthropic_key_and_says_which_model() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_output_evaluator(make_settings())
    evaluator, model = build_output_evaluator(make_settings(anthropic_api_key="sk-ant-test-only"))
    assert isinstance(evaluator, OutputEvaluator) and model == "claude-opus-5-5"
