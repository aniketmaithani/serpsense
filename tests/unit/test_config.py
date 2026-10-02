import pytest
from pydantic import ValidationError

from tests.conftest import TEST_SECRET_KEY, make_settings

pytestmark = pytest.mark.unit

PRODUCTION_OK = {
    "app_env": "production",
    "base_url": "https://serpsense.ai",
    "signup_mode": "invite",
    "email_backend": "smtp",
}


def test_settings_load_when_development_defaults() -> None:
    settings = make_settings(app_env="development")
    assert settings.is_production is False


def test_settings_accept_production_when_all_guards_pass() -> None:
    assert make_settings(**PRODUCTION_OK).is_production is True


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"email_backend": "console"}, "EMAIL_BACKEND=console"),
        ({"signup_mode": "open"}, "SIGNUP_MODE"),
        ({"base_url": "http://serpsense.ai"}, "https"),
    ],
)
def test_settings_reject_production_when_unsafe(override: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make_settings(**{**PRODUCTION_OK, **override})


def test_settings_reject_short_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make_settings(secret_key="too-short")


def test_settings_never_expose_secrets_in_repr() -> None:
    settings = make_settings(serpapi_api_key="serp-test-value", anthropic_api_key="claude-test")
    rendered = repr(settings)
    assert TEST_SECRET_KEY not in rendered
    assert "serp-test-value" not in rendered
    assert "claude-test" not in rendered


def test_allowed_model_ids_split_and_trim() -> None:
    settings = make_settings(allowed_models=" claude-opus-5-5 , claude-haiku-4-5 ,")
    assert settings.allowed_model_ids == ("claude-opus-5-5", "claude-haiku-4-5")
