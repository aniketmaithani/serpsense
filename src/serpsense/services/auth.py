"""Sign in with an emailed code (ADR-0009).

One flow signs up and logs in. Asking for a code answers the same way whether the address is
known, unknown, not invited or rate limited, so the answer reveals nothing. A code is six digits
from `secrets`, kept only as an HMAC, sealed in its email and sent through the outbox at once
(the outbox is nudged after commit). Verifying locks the email's live code, refuses an expired one
or one with five wrong guesses before comparing, and records every comparison; the first
success creates the user and opens a session: its token is 32 random bytes, kept only as its
SHA-256, and `services/sessions.py` keeps it from there. Codes, tokens and emails are never logged.
"""

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from ipaddress import IPv4Address, IPv6Address, ip_network

from serpsense.domain import auth
from serpsense.domain.enums import AuditAction, AuditTarget
from serpsense.observability import get_logger
from serpsense.ports.accounts import InvalidEmail, email_address
from serpsense.ports.audit import AuditEntry, Network
from serpsense.ports.clock import Clock
from serpsense.ports.rate_limiter import RateLimiter
from serpsense.ports.secret_box import SecretBox
from serpsense.ports.sessions import NewSession
from serpsense.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

log = get_logger(__name__)

REQUESTS_PER_IP, VERIFIES_PER_IP = 20, 50  # an hour (ADR-0009)


@dataclass(frozen=True)
class AuthKeys:
    """Derived from SECRET_KEY per purpose in composition; the service never sees the secret."""

    otp: bytes = field(repr=False)
    csrf: bytes = field(repr=False)


@dataclass(frozen=True)
class SignInPorts:
    unit_of_work: UnitOfWorkFactory
    box: SecretBox
    limiter: RateLimiter
    clock: Clock


@dataclass(frozen=True)
class SignedIn:
    token: str = field(repr=False)  # for the cookie; never stored or logged
    user_id: uuid.UUID


class SignIn:
    def __init__(
        self, ports: SignInPorts, keys: AuthKeys, policy: auth.SignupPolicy, *, session_days: int
    ) -> None:
        self._ports, self._keys, self._policy = ports, keys, policy
        self._session_length = timedelta(days=session_days)

    def request_code(self, raw_email: str, network: Network) -> None:
        """Send a code if the address may have one; raises InvalidEmail for a malformed address
        (saying so reveals nothing), and otherwise answers nothing."""
        email = email_address(raw_email)
        if not self._ports.limiter.allow(
            _key("request", network.ip), limit=REQUESTS_PER_IP, window=auth.HOUR
        ):
            log.warning("otp.request_limited")
            return
        if not self._policy.allows(email):
            log.info("otp.request_refused")
            return
        with self._ports.unit_of_work() as uow:
            code_id = self._issue(uow, email, network)
        if code_id is not None:
            log.info("otp.requested", otp_code_id=str(code_id))

    def verify(self, raw_email: str, code: str, network: Network) -> SignedIn | None:
        """A new session for the right code; None for anything else, all alike."""
        try:
            email = email_address(raw_email)
        except InvalidEmail:
            return None
        if not self._ports.limiter.allow(
            _key("verify", network.ip), limit=VERIFIES_PER_IP, window=auth.HOUR
        ):
            log.warning("otp.verify_limited")
            return None
        if not self._policy.allows(email):
            return None
        with self._ports.unit_of_work() as uow:
            signed_in = self._verify(uow, email, code, network)
        if signed_in is None:
            log.info("otp.verify_failed")
        else:
            log.info("auth.login_succeeded", user_id=str(signed_in.user_id))
        return signed_in

    def _issue(self, uow: UnitOfWork, email: str, network: Network) -> uuid.UUID | None:
        at = self._ports.clock.now()
        uow.otp_codes.lock_requests(email)
        if uow.otp_codes.issued_in_all_since(at - auth.HOUR) >= auth.CODES_PER_HOUR_IN_ALL:
            log.warning("otp.global_limit")
            return None
        if not auth.may_request(uow.otp_codes.issued_since(email, at - auth.HOUR), at):
            return None
        code = f"{secrets.randbelow(10**auth.CODE_DIGITS):0{auth.CODE_DIGITS}d}"
        code_hash = auth.otp_hash(self._keys.otp, email, code)
        code_id = uow.otp_codes.issue(
            email, code_hash, at=at, expires_at=at + auth.CODE_TTL, ip=network.ip
        )
        if code_id is None:  # a parallel request's code went live
            return None
        minutes = int(auth.CODE_TTL.total_seconds() // 60)
        sealed = self._ports.box.seal(code.encode())
        if uow.outbox.add_otp_email(code_id, sealed=sealed, minutes=minutes, at=at):
            uow.jobs.dispatch_outbox()
        target = (AuditTarget.OTP_CODE, code_id)
        uow.audit.record(AuditEntry(AuditAction.CODE_REQUESTED, at, target=target, network=network))
        return code_id

    def _verify(self, uow: UnitOfWork, email: str, code: str, network: Network) -> SignedIn | None:
        at = self._ports.clock.now()
        live = uow.otp_codes.lock_live(email)
        if live is None:
            return None
        usable = live.expires_at > at and live.failed_attempts < auth.MAX_ATTEMPTS
        matched = (
            usable
            and auth.is_code(code)
            and auth.code_matches(self._keys.otp, email, code, live.code_hash)
        )
        if not usable:  # nothing compared, so nothing recorded or audited
            return None
        uow.otp_codes.attempt(live.code_id, succeeded=matched, at=at)
        if not matched:
            target = (AuditTarget.OTP_CODE, live.code_id)
            entry = AuditEntry(AuditAction.VERIFY_FAILED, at, target=target, network=network)
            uow.audit.record(entry)
            return None
        uow.otp_codes.consume(live.code_id, at=at)
        return self._open_session(uow, uow.accounts.user_for(email, at=at), network)

    def _open_session(self, uow: UnitOfWork, user_id: uuid.UUID, network: Network) -> SignedIn:
        at = self._ports.clock.now()
        token = secrets.token_urlsafe(32)
        new = NewSession(
            user_id=user_id,
            token_hash=auth.token_hash(token),
            csrf_secret=secrets.token_bytes(32),
            at=at,
            expires_at=at + self._session_length,
            ip=network.ip,
            user_agent=network.user_agent,
        )
        uow.sessions.create(new)
        target = (AuditTarget.USER, user_id)
        entry = AuditEntry(
            AuditAction.LOGIN_SUCCEEDED, at, actor_user_id=user_id, target=target, network=network
        )
        uow.audit.record(entry)
        return SignedIn(token, user_id)


def _key(action: str, ip: IPv4Address | IPv6Address | None) -> str:
    """One count per IPv4 address, and per IPv6 /64, which one client usually holds whole."""
    source = ip_network(f"{ip}/64", strict=False) if isinstance(ip, IPv6Address) else ip
    return f"otp-{action}:{source or 'unknown'}"
