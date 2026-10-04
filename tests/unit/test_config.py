import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from serpsense.config import (
    EmailBackend,
    LlmPreset,
    LogLevel,
    MigrationSettings,
    RunMode,
    SignupMode,
)
from tests.factories import TEST_FERNET_KEY, TEST_SECRET_KEY, make_settings

pytestmark = pytest.mark.unit

PRODUCTION_OK = {
    "app_env": "production",
    "base_url": "https://serpsense.ai",
    "signup_mode": "invite",
    "email_backend": "smtp",
    "smtp_starttls": "true",
    "database_url": "postgresql+psycopg://app:prod-pw@db.internal:5432/serpsense",
}


def test_settings_defaults_when_development() -> None:
    settings = make_settings(app_env="development")
    assert settings.is_production is False
    assert settings.email_backend is EmailBackend.SMTP
    assert settings.signup_mode is SignupMode.OPEN
    assert settings.serpsense_mode is RunMode.LIVE
    assert settings.default_llm_preset is LlmPreset.BALANCED
    assert settings.log_level is LogLevel.INFO


def test_settings_accept_production_when_all_guards_pass() -> None:
    assert make_settings(**PRODUCTION_OK).is_production is True


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"email_backend": "console"}, "EMAIL_BACKEND=console"),
        ({"smtp_starttls": "false"}, "SMTP_STARTTLS"),
        ({"signup_mode": "open"}, "SIGNUP_MODE"),
        ({"base_url": "http://serpsense.ai"}, "https"),
        ({"serpsense_mode": "replay"}, "SERPSENSE_MODE=replay"),
        (
            {"database_url": "postgresql+psycopg://serpsense:serpsense@postgres:5432/serpsense"},
            "development credentials",
        ),
    ],
)
def test_settings_reject_production_when_unsafe(override: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make_settings(**{**PRODUCTION_OK, **override})


def test_settings_reject_short_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make_settings(secret_key="too-short")


@pytest.mark.parametrize("keys", ["", "not-base64!", "c2hvcnQ=", f"{TEST_FERNET_KEY},bad"])
def test_settings_reject_invalid_outbox_keys(keys: str) -> None:
    with pytest.raises(ValidationError, match="OUTBOX_ENCRYPTION_KEYS"):
        make_settings(outbox_encryption_keys=keys)


def test_settings_accept_multiple_outbox_keys_for_rotation() -> None:
    other = "B" * 43 + "="
    settings = make_settings(outbox_encryption_keys=f"{TEST_FERNET_KEY}, {other}")
    assert other in settings.secret_values()


@pytest.mark.parametrize("raw", ["debug", "WARNING", "Error"])
def test_log_level_is_case_insensitive(raw: str) -> None:
    assert make_settings(log_level=raw).log_level is LogLevel(raw.upper())


def test_log_level_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        make_settings(log_level="verbose")


def test_settings_never_expose_secrets_in_repr() -> None:
    settings = make_settings(serpapi_api_key="serp-test-value", anthropic_api_key="claude-test")
    rendered = repr(settings)
    assert TEST_SECRET_KEY not in rendered
    assert "serp-test-value" not in rendered
    assert "claude-test" not in rendered


def test_secret_values_lists_every_configured_secret() -> None:
    settings = make_settings(
        serpapi_api_key="serp-test-value",
        smtp_password="smtp-pass-123",
        admin_password="console-pass-0123",
    )
    values = settings.secret_values()
    expected = {TEST_SECRET_KEY, "serp-test-value", "smtp-pass-123", "console-pass-0123"}
    assert expected | {TEST_FERNET_KEY} <= set(values)


def test_the_operator_console_is_off_without_a_password_and_needs_a_long_one() -> None:
    assert make_settings().admin_password is None  # ADR-0014: no password, no console
    assert make_settings(admin_password="").admin_password is None  # as .env.example has it
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        make_settings(admin_password="short-but-15ch!")
    assert "x" * 16 not in repr(make_settings(admin_password="x" * 16))


def test_csv_settings_are_split_trimmed_and_lowercased() -> None:
    settings = make_settings(
        allowed_models=" claude-opus-5-5 , claude-haiku-4-5 ,",
        allowed_emails="A@Example.com, b@example.com",
        allowed_domains="Example.COM,",
    )
    assert settings.allowed_model_ids == ("claude-opus-5-5", "claude-haiku-4-5")
    assert settings.allowed_email_list == ("a@example.com", "b@example.com")
    assert settings.allowed_domain_list == ("example.com",)


def test_migration_settings_need_only_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://m:m@localhost:5432/m")
    assert MigrationSettings().database_url.get_secret_value().endswith("/m")


def test_the_outbox_keys_are_listed_newest_first() -> None:
    new = Fernet.generate_key().decode()
    settings = make_settings(outbox_encryption_keys=f"{new}, {TEST_FERNET_KEY}")
    assert settings.outbox_key_list == (new, TEST_FERNET_KEY)
