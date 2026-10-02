import logging
from collections.abc import Iterator

import pytest
import structlog

from serpsense.observability import (
    REDACTED,
    ValueScrubber,
    configure_logging,
    drop_forbidden_keys,
    get_logger,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "serpapi_api_key",
        "email",
        "recipient_email",
        "code",
        "otp",
        "session",
        "session_token",
        "csrf",
        "set_cookie",
        "password",
        "smtp_password",
        "secret_key",
        "outbox_encryption_keys",
        "database_url",
        "serpapi_link",
        "prompt",
        "completion",
        "payload",
        "raw_payload",
        "secret",
    ],
)
def test_forbidden_keys_are_redacted(key: str) -> None:
    event = drop_forbidden_keys(None, "info", {"event": "x.happened", key: "sensitive"})
    assert event[key] == REDACTED


@pytest.mark.parametrize("key", ["status_code", "error_code", "user_id", "scan_id", "route"])
def test_diagnostic_keys_are_kept(key: str) -> None:
    event = drop_forbidden_keys(None, "info", {"event": "x.happened", key: "visible"})
    assert event[key] == "visible"


def test_event_name_is_never_redacted_by_key_rule() -> None:
    event = drop_forbidden_keys(None, "info", {"event": "token.rotated"})
    assert event["event"] == "token.rotated"


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("GET https://serpapi.com/search?q=x&api_key=abc123def", "abc123def"),
        ("connect postgresql://app:hunter2pw@db:5432/x", "hunter2pw"),
        ("retry with token=tok_live_999", "tok_live_999"),
    ],
)
def test_value_scrubber_removes_secret_patterns(raw: str, leaked: str) -> None:
    scrubbed = ValueScrubber().scrub(raw)
    assert leaked not in scrubbed
    assert REDACTED in scrubbed


def test_value_scrubber_removes_known_secret_values_anywhere() -> None:
    scrubber = ValueScrubber(known_secrets=["s3cret-value-xyz", "short"])
    event = scrubber(
        None, "error", {"event": "boom s3cret-value-xyz", "exception": "x s3cret-value-xyz"}
    )
    assert "s3cret-value-xyz" not in event["event"]
    assert "s3cret-value-xyz" not in event["exception"]


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


@pytest.mark.usefixtures("restore_logging")
def test_stdlib_and_structlog_records_are_scrubbed(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="DEBUG", json=True, known_secrets=["configured-secret-123"])
    logging.getLogger("third.party").warning(
        "calling ?api_key=leakme999 with configured-secret-123"
    )
    get_logger("ours").info("thing.done", recipient_email="a@b.c", status_code=200)
    out = capsys.readouterr().out
    assert "leakme999" not in out
    assert "configured-secret-123" not in out
    assert "a@b.c" not in out
    assert '"status_code": 200' in out


@pytest.mark.usefixtures("restore_logging")
def test_exception_text_is_scrubbed(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", json=True)
    try:
        raise RuntimeError("failed for https://x.test/?api_key=leakme777")
    except RuntimeError:
        get_logger("ours").exception("thing.failed")
    out = capsys.readouterr().out
    assert "leakme777" not in out
    assert "thing.failed" in out


@pytest.mark.usefixtures("restore_logging")
def test_http_client_loggers_are_capped_at_warning() -> None:
    configure_logging(level="DEBUG", json=True)
    assert logging.getLogger("urllib3").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
