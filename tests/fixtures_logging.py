"""Fixture that restores global logging state after tests that reconfigure it."""

import logging
from collections.abc import Iterator

import pytest
import structlog

_TOUCHED = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.task")


@pytest.fixture
def restore_logging() -> Iterator[None]:
    saved = {
        name: (lg.handlers[:], lg.level, lg.propagate, lg.filters[:])
        for name in _TOUCHED
        for lg in [logging.getLogger(name)]
    }
    yield
    for name, (handlers, level, propagate, filters) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers, lg.level, lg.propagate, lg.filters = handlers, level, propagate, filters
    structlog.reset_defaults()
