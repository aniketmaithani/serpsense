import pytest

from serpsense.observability import REDACTED, drop_forbidden_keys

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
        "session_token",
        "password",
        "smtp_password",
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


@pytest.mark.parametrize("key", ["status_code", "error_code", "user_id", "scan_id", "engine"])
def test_diagnostic_keys_are_kept(key: str) -> None:
    event = drop_forbidden_keys(None, "info", {"event": "x.happened", key: "visible"})
    assert event[key] == "visible"


def test_event_name_is_never_redacted() -> None:
    event = drop_forbidden_keys(None, "info", {"event": "token.rotated"})
    assert event["event"] == "token.rotated"
