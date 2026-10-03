import uuid
from datetime import timedelta

import pytest

from serpsense.adapters.llm.profiles import PresetProfiles
from serpsense.composition import (
    COLLECTION_TIME,
    SCAN_TIME_LIMIT_SECONDS,
    STUCK_AFTER,
    build_celery,
    build_container,
    build_worker,
)
from serpsense.config import ConfigError, Settings
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import LlmPreset, preset_settings
from serpsense.services.dispatch import Dispatcher
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
