"""Shared structured logging (ADR-0012). Use `get_logger` everywhere; never `print`."""

import logging
import sys

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

# Keys whose values must never reach logs (AGENTS.md §8). Matched exactly or by suffix,
# so diagnostic keys such as `status_code` and `error_code` stay visible.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "code",
        "completion",
        "cookie",
        "email",
        "otp",
        "password",
        "payload",
        "prompt",
        "secret",
        "token",
    }
)
FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    "_api_key",
    "_completion",
    "_email",
    "_otp",
    "_password",
    "_payload",
    "_prompt",
    "_secret",
    "_token",
)
REDACTED = "[redacted]"


def is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in FORBIDDEN_KEYS or lowered.endswith(FORBIDDEN_SUFFIXES)


def drop_forbidden_keys(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Replace values of sensitive keys so they never leave the process."""
    for key in list(event_dict):
        if key != "event" and is_forbidden_key(key):
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(*, level: str = "INFO", json: bool = True) -> None:
    """Configure structlog once per process (web, worker, CLI)."""
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            drop_forbidden_keys,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
