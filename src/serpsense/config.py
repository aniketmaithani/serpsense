"""Application settings. The only module that reads environment variables (AGENTS.md §3)."""

from enum import StrEnum

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_KEY_LENGTH = 32


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class RunMode(StrEnum):
    LIVE = "live"
    REPLAY = "replay"


class EmailBackend(StrEnum):
    SMTP = "smtp"
    CONSOLE = "console"


class SignupMode(StrEnum):
    OPEN = "open"
    INVITE = "invite"


class ConfigError(ValueError):
    """Raised when settings are unsafe for the selected environment."""


class Settings(BaseSettings):
    """Typed settings loaded from the process environment."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    # Core
    app_env: AppEnv = AppEnv.DEVELOPMENT
    base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    secret_key: SecretStr
    outbox_encryption_keys: SecretStr
    database_url: SecretStr
    redis_url: SecretStr
    serpsense_mode: RunMode = RunMode.LIVE

    # SerpApi
    serpapi_api_key: SecretStr | None = None
    max_searches_per_scan: int = Field(default=40, ge=1, le=200)
    serpapi_daily_global_cap: int = Field(default=500, ge=1)
    default_monthly_search_budget: int = Field(default=1500, ge=0)

    # Claude
    anthropic_api_key: SecretStr | None = None
    allowed_models: str = "claude-opus-5-5,claude-sonnet-5-5,claude-haiku-4-5"
    default_llm_preset: str = "balanced"
    default_monthly_llm_budget_micros: int = Field(default=30_000_000, ge=0)
    llm_refusal_fallback: bool = True

    # Auth
    session_days: int = Field(default=7, ge=1, le=30)
    otp_ttl_minutes: int = Field(default=10, ge=1, le=30)
    signup_mode: SignupMode = SignupMode.OPEN
    allowed_emails: str = ""
    allowed_domains: str = ""

    # Email
    email_backend: EmailBackend = EmailBackend.SMTP
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    email_from: str = "SerpSense <no-reply@serpsense.local>"

    # Observability
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def allowed_model_ids(self) -> tuple[str, ...]:
        return tuple(m.strip() for m in self.allowed_models.split(",") if m.strip())

    @model_validator(mode="after")
    def _check_safety(self) -> "Settings":
        if len(self.secret_key.get_secret_value()) < MIN_SECRET_KEY_LENGTH:
            raise ConfigError(f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters")
        if self.is_production:
            _check_production(self)
        return self


def _check_production(settings: Settings) -> None:
    """Production guards from ADR-0009 and ADR-0010."""
    if settings.email_backend is EmailBackend.CONSOLE:
        raise ConfigError("EMAIL_BACKEND=console is not allowed in production")
    if settings.signup_mode is not SignupMode.INVITE:
        raise ConfigError("SIGNUP_MODE must be 'invite' in production")
    if settings.base_url.scheme != "https":
        raise ConfigError("BASE_URL must use https in production")
