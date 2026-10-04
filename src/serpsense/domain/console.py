"""The operator console's password check and signed session (ADR-0014).

Pure: the keys, the time and the random values are given. A typed password is compared with the
configured one as HMACs, in constant time. A session is `<expiry>.<nonce>.<signature>`, signed
with a key derived from `SECRET_KEY` and the password, so changing either signs the operator
out; nothing about it is stored. A console form's CSRF token is an HMAC of the session.
"""

import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SESSION_LENGTH = timedelta(hours=4)
LOGINS_PER_SOURCE, PER_SOURCE_WINDOW = 5, timedelta(minutes=15)
LOGINS_IN_ALL, IN_ALL_WINDOW = 30, timedelta(hours=1)  # every source together


@dataclass(frozen=True)
class ConsoleKeys:
    """Made by `console_keys` in composition; services never see the secret or the password."""

    check: bytes = field(repr=False)  # hashes a typed password
    password: bytes = field(repr=False)  # the configured password, hashed with `check`
    session: bytes = field(repr=False)
    csrf: bytes = field(repr=False)


def console_keys(base: bytes, password: str) -> ConsoleKeys:
    """`base` is derived from SECRET_KEY for the console; the session and CSRF keys also depend
    on the password."""
    secret = password.encode()
    return ConsoleKeys(
        check=base,
        password=_mac(base, "password", secret),
        session=_mac(base, "session", secret),
        csrf=_mac(base, "csrf", secret),
    )


def password_matches(keys: ConsoleKeys, typed: str) -> bool:
    typed_hash = _mac(keys.check, "password", typed.encode("utf-8", "replace"))
    return hmac.compare_digest(typed_hash, keys.password)


def session_token(keys: ConsoleKeys, expires_at: datetime, nonce: str) -> str:
    body = f"{int(expires_at.timestamp())}.{nonce}"
    return f"{body}.{_signature(keys, body)}"


def session_valid(keys: ConsoleKeys, token: str, at: datetime) -> bool:
    """A session this key signed, not yet expired; anything else is just invalid."""
    body, _, signature = token.rpartition(".")
    expected = _signature(keys, body).encode()
    if not body or not hmac.compare_digest(expected, signature.encode("utf-8", "replace")):
        return False
    expiry = body.partition(".")[0]
    return expiry.isascii() and expiry.isdigit() and at.timestamp() < int(expiry)


def csrf_token(keys: ConsoleKeys, session: str) -> str:
    return _b64(_mac(keys.csrf, "csrf", session.encode("utf-8", "replace")))


def csrf_valid(keys: ConsoleKeys, session: str, submitted: str) -> bool:
    expected = csrf_token(keys, session).encode()
    return hmac.compare_digest(expected, submitted.encode("utf-8", "replace"))


def _signature(keys: ConsoleKeys, body: str) -> str:
    return _b64(_mac(keys.session, "session", body.encode("utf-8", "replace")))


def _mac(key: bytes, purpose: str, value: bytes) -> bytes:
    return hmac.new(key, purpose.encode() + b"\0" + value, hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
