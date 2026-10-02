"""Shared structured logging (ADR-0012). Use `get_logger` everywhere; never `print`.

All log records — ours and third-party stdlib loggers (uvicorn, celery, sqlalchemy, urllib3) —
go through one handler whose processors redact sensitive keys and scrub secret-looking values,
including exception text (AGENTS.md §8).
"""

import logging
import re
import sys
from collections.abc import Iterable

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

# Keys whose values must never reach logs. Matched exactly or by suffix, so diagnostic keys
# such as `status_code`, `error_code` and `route` stay visible.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "code",
        "completion",
        "cookie",
        "csrf",
        "email",
        "otp",
        "password",
        "payload",
        "prompt",
        "secret",
        "session",
        "set_cookie",
        "token",
    }
)
FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    "_api_key",
    "_completion",
    "_csrf",
    "_email",
    "_key",
    "_keys",
    "_link",
    "_otp",
    "_password",
    "_payload",
    "_prompt",
    "_secret",
    "_token",
    "_url",
)
REDACTED = "[redacted]"

# Value patterns scrubbed from every string field (and exception text).
_QUERY_SECRET = re.compile(
    r"(?i)\b(api_key|apikey|access_token|token|password|secret|key)=([^&\s\"']+)"
)
# Userinfo is everything in the URL authority up to its last "@" (so an unencoded "@" in a
# password is covered); the authority ends at "/", "?", "#" or whitespace, so "@" in a path or
# query is never touched. Configured secrets (e.g. DATABASE_URL) are also scrubbed verbatim.
_URL_USERINFO = re.compile(r"://([^/?#\s]*)@")
_NOISY_LOGGERS = ("urllib3", "requests", "httpx", "httpcore", "celery.utils.functional")
# Frameworks that install their own handlers (often with propagate=False) before our setup runs;
# we strip those so every record reaches the scrubbing root handler.
_FRAMEWORK_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.task")
# uvicorn re-logs exceptions our 500 handler already logged with request context.
_UVICORN_DUPLICATE_ERROR = "Exception in ASGI application"


def _redact_userinfo(match: re.Match[str]) -> str:
    userinfo = match.group(1)
    return f"://{REDACTED}@" if ":" in userinfo else match.group(0)


def is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in FORBIDDEN_KEYS or lowered.endswith(FORBIDDEN_SUFFIXES)


def drop_forbidden_keys(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Replace values of sensitive keys so they never leave the process."""
    for key in list(event_dict):
        if key != "event" and is_forbidden_key(key):
            event_dict[key] = REDACTED
    return event_dict


class ValueScrubber:
    """Scrub secret-looking substrings and known secret values from all string fields."""

    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        self._known = tuple(
            sorted({s for s in known_secrets if len(s) >= 8}, key=len, reverse=True)
        )

    def scrub(self, text: str) -> str:
        for secret in self._known:
            text = text.replace(secret, REDACTED)
        text = _QUERY_SECRET.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
        return _URL_USERINFO.sub(_redact_userinfo, text)

    def __call__(self, _: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        for key, value in event_dict.items():
            if isinstance(value, str):
                event_dict[key] = self.scrub(value)
        return event_dict


def configure_logging(
    *, level: str = "INFO", json: bool = True, known_secrets: Iterable[str] = ()
) -> None:
    """Configure structlog and the stdlib root logger once per process (web, worker, CLI)."""
    numeric_level = logging.getLevelName(level.upper())
    scrubber = ValueScrubber(known_secrets)
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        drop_forbidden_keys,
        scrubber,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)
    for name in _FRAMEWORK_LOGGERS:
        framework_logger = logging.getLogger(name)
        framework_logger.handlers = []
        framework_logger.propagate = True
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.filters = [f for f in uvicorn_error.filters if not isinstance(f, _DropDuplicate)]
    uvicorn_error.addFilter(_DropDuplicate(_UVICORN_DUPLICATE_ERROR))
    # HTTP client libraries log full request URLs (SerpApi puts the key in the query string).
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, numeric_level))


class _DropDuplicate(logging.Filter):
    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn logs the message with a trailing newline.
        return record.getMessage().strip() != self._message


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
