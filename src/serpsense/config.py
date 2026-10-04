"""Application settings. The only module that reads environment variables (AGENTS.md §3)."""

import base64
import binascii
from enum import StrEnum

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from serpsense.domain.llm_capabilities import LlmPreset

MIN_SECRET_KEY_LENGTH = 32
MIN_ADMIN_PASSWORD_LENGTH = 16  # ADR-0014
FERNET_KEY_BYTES = 32
# Credentials hard-coded for local development in docker-compose.yml; never valid in production.
DEV_DATABASE_CREDENTIALS = "serpsense:serpsense@"


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


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ConfigError(ValueError):
    """Raised when settings are unsafe for the selected environment."""


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


class MigrationSettings(BaseSettings):
    """The subset Alembic needs: just the database URL."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    database_url: SecretStr


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
    default_llm_preset: LlmPreset = LlmPreset.BALANCED
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

    # Operator console (ADR-0014): off unless a password is set
    admin_password: SecretStr | None = None

    # Observability
    log_level: LogLevel = LogLevel.INFO

    @field_validator("admin_password", mode="before")
    @classmethod
    def _empty_admin_password_is_none(cls, value: object) -> object:
        return None if value == "" else value  # `ADMIN_PASSWORD=` as copied from .env.example

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def allowed_model_ids(self) -> tuple[str, ...]:
        return _split_csv(self.allowed_models)

    @property
    def allowed_email_list(self) -> tuple[str, ...]:
        return tuple(e.lower() for e in _split_csv(self.allowed_emails))

    @property
    def allowed_domain_list(self) -> tuple[str, ...]:
        return tuple(d.lower() for d in _split_csv(self.allowed_domains))

    @property
    def outbox_key_list(self) -> tuple[str, ...]:
        """OUTBOX_ENCRYPTION_KEYS, newest first (validated at load)."""
        return _split_csv(self.outbox_encryption_keys.get_secret_value())

    def secret_values(self) -> tuple[str, ...]:
        """All configured secret strings, for log scrubbing."""
        secrets = (
            self.secret_key,
            self.outbox_encryption_keys,
            self.database_url,
            self.redis_url,
            self.serpapi_api_key,
            self.anthropic_api_key,
            self.smtp_password,
            self.admin_password,
        )
        values = [s.get_secret_value() for s in secrets if s is not None]
        values.extend(_split_csv(self.outbox_encryption_keys.get_secret_value()))
        return tuple(v for v in values if v)

    @model_validator(mode="after")
    def _check_safety(self) -> "Settings":
        if len(self.secret_key.get_secret_value()) < MIN_SECRET_KEY_LENGTH:
            raise ConfigError(f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters")
        _check_fernet_keys(self.outbox_encryption_keys.get_secret_value())
        admin = self.admin_password
        if admin is not None and len(admin.get_secret_value()) < MIN_ADMIN_PASSWORD_LENGTH:
            raise ConfigError(
                f"ADMIN_PASSWORD must be at least {MIN_ADMIN_PASSWORD_LENGTH} characters"
            )
        if self.is_production:
            _check_production(self)
        return self


def _check_fernet_keys(raw: str) -> None:
    keys = _split_csv(raw)
    if not keys:
        raise ConfigError("OUTBOX_ENCRYPTION_KEYS must contain at least one key")
    for key in keys:
        try:
            decoded = base64.urlsafe_b64decode(key.encode())
        except (binascii.Error, ValueError) as exc:
            raise ConfigError("OUTBOX_ENCRYPTION_KEYS contains an invalid key") from exc
        if len(decoded) != FERNET_KEY_BYTES:
            raise ConfigError("OUTBOX_ENCRYPTION_KEYS contains an invalid key")


def _check_production(settings: Settings) -> None:
    """Production guards from ADR-0009, ADR-0010 and the bootstrap security review."""
    if settings.serpsense_mode is RunMode.REPLAY:
        raise ConfigError("SERPSENSE_MODE=replay plays back recorded scans; not for production")
    if settings.email_backend is EmailBackend.CONSOLE:
        raise ConfigError("EMAIL_BACKEND=console is not allowed in production")
    if not settings.smtp_starttls:
        raise ConfigError("SMTP_STARTTLS must be true in production")
    if settings.signup_mode is not SignupMode.INVITE:
        raise ConfigError("SIGNUP_MODE must be 'invite' in production")
    if settings.base_url.scheme != "https":
        raise ConfigError("BASE_URL must use https in production")
    if DEV_DATABASE_CREDENTIALS in settings.database_url.get_secret_value():
        raise ConfigError("DATABASE_URL uses the development credentials in production")
