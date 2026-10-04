"""Sign-in by an emailed code (ADR-0009): hashes, tokens and limits.

Pure: the keys, the time and the random values are given. A code is stored only as
HMAC-SHA256(K_otp, email:code) and a session token only as its SHA-256; comparisons are constant
time. Emails are compared without case, as the database does (citext).
"""

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv6Address, ip_network

CODE_DIGITS = 6
CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5  # guesses per code
RESEND_AFTER = timedelta(seconds=60)
CODES_PER_HOUR = 5  # per email
HOUR = timedelta(hours=1)
SESSION_REFRESH = timedelta(hours=1)  # a session's expiry slides at most this often
MAX_SESSION_AGE = timedelta(days=30)  # however often it slides, a session ends by then
CODES_PER_HOUR_IN_ALL = 200  # every address together: bounds a spray, or a limiter that's down
ACCESS_REQUESTS_PER_HOUR_IN_ALL = 50  # new access requests, every address together (ADR-0014)
MAX_USER_AGENT = 256


def otp_hash(key: bytes, email: str, code: str) -> bytes:
    return hmac.new(key, f"{email.lower()}:{code}".encode(), hashlib.sha256).digest()


def code_matches(key: bytes, email: str, code: str, stored: bytes) -> bool:
    return hmac.compare_digest(otp_hash(key, email, code), stored)


def is_code(text: str) -> bool:
    """Six ASCII digits; anything else is a wrong guess without being hashed."""
    return len(text) == CODE_DIGITS and text.isascii() and text.isdigit()


def token_hash(token: str) -> bytes:
    """What the database keeps of a session token: its SHA-256."""
    return hashlib.sha256(token.encode()).digest()


def csrf_token(key: bytes, session_secret: bytes) -> str:
    return hmac.new(key, session_secret, hashlib.sha256).hexdigest()


def csrf_valid(key: bytes, session_secret: bytes, token: str) -> bool:
    """Bytes are compared, so a token with any characters is just wrong, never an error."""
    expected = csrf_token(key, session_secret).encode()
    return hmac.compare_digest(expected, token.encode("utf-8", "replace"))


def request_source(ip: IPv4Address | IPv6Address | None) -> str:
    """What a per-source limit counts: an IPv4 address, or an IPv6 /64, which one client usually
    holds whole."""
    source = ip_network(f"{ip}/64", strict=False) if isinstance(ip, IPv6Address) else ip
    return str(source or "unknown")


def may_request(recent: Sequence[datetime], at: datetime) -> bool:
    """Whether an email may get another code: none in the last minute, and fewer than five in
    the last hour (`recent` are when its codes were issued)."""
    if any(at - issued < RESEND_AFTER for issued in recent):
        return False
    return sum(at - issued < HOUR for issued in recent) < CODES_PER_HOUR


def clean_user_agent(raw: str | None) -> str | None:
    """A user agent as it is kept: printable characters only, at most 256 of them."""
    cleaned = "".join(ch for ch in raw or "" if ch.isprintable())[:MAX_USER_AGENT]
    return cleaned or None


@dataclass(frozen=True)
class SignupPolicy:
    """Who may get a code: anyone, or in invite mode the listed addresses and domains
    (ADR-0009; production runs invite mode), and those the operator approved (ADR-0014, read
    from the database by the sign-in service)."""

    invite_only: bool
    emails: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()

    def allows(self, email: str) -> bool:
        address = email.lower()
        domain = address.rpartition("@")[2]
        return not self.invite_only or address in self.emails or domain in self.domains
