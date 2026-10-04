"""The operator console's password check and signed session (ADR-0014)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from serpsense.domain import console

pytestmark = [pytest.mark.unit, pytest.mark.security]

BASE = b"k" * 32
KEYS = console.console_keys(BASE, "correct horse battery")
AT = datetime(2026, 10, 4, 12, tzinfo=UTC)


def test_only_the_password_matches() -> None:
    assert console.password_matches(KEYS, "correct horse battery")
    for wrong in ("correct horse batter", "", "correct horse battery ", "\ud800"):
        assert not console.password_matches(KEYS, wrong)


def test_the_keys_never_show_and_a_new_password_or_secret_signs_out() -> None:
    assert "kkkk" not in repr(KEYS) and "correct" not in repr(KEYS)
    token = console.session_token(KEYS, AT + console.SESSION_LENGTH, "nonce")
    for other in (
        console.console_keys(BASE, "another password!!"),
        console.console_keys(b"s" * 32, "correct horse battery"),
    ):
        assert (other.session, other.csrf) != (KEYS.session, KEYS.csrf)
        assert not console.session_valid(other, token, AT)


def test_a_session_lasts_until_it_expires() -> None:
    token = console.session_token(KEYS, AT + console.SESSION_LENGTH, "n0nce")
    assert console.session_valid(KEYS, token, AT)
    assert console.session_valid(KEYS, token, AT + console.SESSION_LENGTH - timedelta(seconds=1))
    assert not console.session_valid(KEYS, token, AT + console.SESSION_LENGTH)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),  # the signature
        lambda t: str(int(t.split(".")[0]) + 3600) + t[t.index(".") :],  # a later expiry
        lambda t: t.rpartition(".")[0],  # no signature
        lambda t: "",
        lambda t: "x.y.z",
        lambda t: t + "\ud800",
    ],
)
def test_a_tampered_session_is_refused(tamper: Callable[[str], str]) -> None:
    token = console.session_token(KEYS, AT + console.SESSION_LENGTH, "n0nce")
    assert not console.session_valid(KEYS, tamper(token), AT)


def test_a_csrf_token_belongs_to_its_session() -> None:
    one = console.session_token(KEYS, AT + console.SESSION_LENGTH, "one")
    two = console.session_token(KEYS, AT + console.SESSION_LENGTH, "two")
    token = console.csrf_token(KEYS, one)
    assert console.csrf_valid(KEYS, one, token)
    assert not console.csrf_valid(KEYS, two, token)
    assert not console.csrf_valid(KEYS, one, "") and not console.csrf_valid(KEYS, one, "\ud800")
