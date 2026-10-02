"""Test data builders shared across test layers."""

from serpsense.config import Settings

# Obviously fake, low-entropy values: never real secrets.
TEST_SECRET_KEY = "unit-test-only-" + "x" * 32
TEST_FERNET_KEY = "A" * 43 + "="


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
