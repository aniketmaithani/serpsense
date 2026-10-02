"""Shared test fixtures."""

import pytest

from serpsense.config import Settings

# Obviously fake, low-entropy values: never real secrets.
TEST_SECRET_KEY = "unit-test-only-" + "x" * 32
TEST_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "secret_key": TEST_SECRET_KEY,
        "outbox_encryption_keys": TEST_FERNET_KEY,
        "database_url": "postgresql+psycopg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Integration tests talk to containers, so they may open sockets."""
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.enable_socket)
