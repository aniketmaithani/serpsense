"""Sign-in's pure rules: code and token hashes, CSRF tokens and request limits (ADR-0009)."""

from datetime import UTC, datetime, timedelta

import pytest

from serpsense.domain import auth

pytestmark = pytest.mark.unit

KEY, OTHER = b"k" * 32, b"o" * 32
AT = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


def test_a_code_matches_only_its_email_and_key() -> None:
    stored = auth.otp_hash(KEY, "Owner@Example.com", "042917")
    assert auth.code_matches(KEY, "owner@example.com", "042917", stored)  # case doesn't matter
    assert not auth.code_matches(KEY, "owner@example.com", "042918", stored)
    assert not auth.code_matches(KEY, "other@example.com", "042917", stored)
    assert not auth.code_matches(OTHER, "owner@example.com", "042917", stored)


@pytest.mark.parametrize(
    ("text", "valid"),
    [("042917", True), ("42917", False), ("0429171", False), ("04291a", False), ("٠٤٢٩١٧", False)],
)
def test_a_code_is_six_ascii_digits(text: str, valid: bool) -> None:
    assert auth.is_code(text) is valid


def test_tokens_and_csrf() -> None:
    assert auth.token_hash("t") == auth.token_hash("t") != auth.token_hash("u")
    assert len(auth.token_hash("t")) == 32
    token = auth.csrf_token(KEY, b"secret")
    assert auth.csrf_valid(KEY, b"secret", token)
    assert not auth.csrf_valid(KEY, b"other secret", token)
    assert not auth.csrf_valid(OTHER, b"secret", token)
    assert not auth.csrf_valid(KEY, b"secret", "tokén")  # any characters: wrong, not an error


def test_a_user_agent_is_kept_printable_and_short() -> None:
    assert auth.clean_user_agent("Mozilla/5.0\x00\n(Mac)") == "Mozilla/5.0(Mac)"
    assert auth.clean_user_agent("x" * 300) == "x" * 256
    assert auth.clean_user_agent("\x00") is auth.clean_user_agent(None) is None


def test_an_email_gets_a_code_a_minute_apart_and_five_an_hour() -> None:
    assert auth.may_request([], AT)
    assert not auth.may_request([AT - timedelta(seconds=59)], AT)
    assert auth.may_request([AT - timedelta(seconds=60)], AT)
    four = [AT - timedelta(minutes=m) for m in (5, 15, 25, 35)]
    assert auth.may_request(four, AT)
    assert not auth.may_request([*four, AT - timedelta(minutes=45)], AT)
    assert auth.may_request([*four, AT - timedelta(minutes=61)], AT)  # an hour ago drops out
