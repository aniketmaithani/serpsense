"""Sign in with an emailed code (ADR-0009).

One flow signs up and logs in. Asking for a code answers the same way whether the address is
known, unknown, not invited or rate limited, so the answer reveals nothing. A code is six digits
from `secrets`, kept only as an HMAC, sealed in its email and sent through the outbox at once
(the outbox is nudged after commit). Verifying locks the email's live code, refuses an expired one
or one with five wrong guesses before comparing, and records every comparison; the first
success creates the user and opens a session: its token is 32 random bytes, kept only as its
SHA-256, and `services/sessions.py` keeps it from there. A signed-in user can be sent a code to
confirm deleting their account (`services/accounts.py`), checked the same way. Codes, tokens and
emails are never logged.
"""

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from ipaddress import IPv4Address, IPv6Address, ip_network

from serpsense.domain import auth
from serpsense.domain.enums import AuditAction, AuditTarget, CodeEmail
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
        if not self._may_request(network):
            return
        if not self._policy.allows(email):
            log.info("otp.request_refused")
            return
        self._send(email, network, CodeEmail.SIGN_IN)

    def send_step_up_code(self, email: str, network: Network, purpose: CodeEmail) -> None:
        """A code to a signed-in user's own address, in an email saying what it is for, to
        confirm what only they may do (deleting their account, ADR-0013): every limit a sign-in
        code has, but no invite check, since they are in already."""
        if self._may_request(network):
            self._send(email, network, purpose)

    def may_verify(self, network: Network) -> bool:
        """Whether the source may try a code now; each call counts as a try (ADR-0009)."""
        if self._ports.limiter.allow(
            _key("verify", network.ip), limit=VERIFIES_PER_IP, window=auth.HOUR
        ):
            return True
        log.warning("otp.verify_limited")
        return False

    def verify(self, raw_email: str, code: str, network: Network) -> SignedIn | None:
        """A new session for the right code; None for anything else, all alike."""
        try:
            email = email_address(raw_email)
        except InvalidEmail:
            return None
        if not self.may_verify(network) or not self._policy.allows(email):
            return None
        with self._ports.unit_of_work() as uow:
            signed_in = self._verify(uow, email, code, network)
        if signed_in is None:
            log.info("otp.verify_failed")
        else:
            log.info("auth.login_succeeded", user_id=str(signed_in.user_id))
        return signed_in

    def check_code(
        self,
        uow: UnitOfWork,
        email: str,
        code: str,
        network: Network,
        *,
        confirmed: bool = True,
    ) -> bool:
        """Whether `code` is the address's live code, in the caller's unit of work: the code is
        locked, an expired or locked-out one refused without comparing, the comparison recorded
        (and audited when wrong), and a right code used up. Unless `confirmed` (what the code is
        for wasn't confirmed: a deletion's typed address didn't match), even the right code
        counts as a wrong guess, after the same work, so the two failures look alike."""
        at = self._ports.clock.now()
        live = uow.otp_codes.lock_live(email)
        if live is None:
            return False
        usable = live.expires_at > at and live.failed_attempts < auth.MAX_ATTEMPTS
        right = (
            usable
            and auth.is_code(code)
            and auth.code_matches(self._keys.otp, email, code, live.code_hash)
        )
        matched = right and confirmed
        if not usable:  # nothing compared, so nothing recorded or audited
            return False
        uow.otp_codes.attempt(live.code_id, succeeded=matched, at=at)
        if not matched:
            target = (AuditTarget.OTP_CODE, live.code_id)
            entry = AuditEntry(AuditAction.VERIFY_FAILED, at, target=target, network=network)
            uow.audit.record(entry)
            return False
        uow.otp_codes.consume(live.code_id, at=at)
        return True

    def _may_request(self, network: Network) -> bool:
        if self._ports.limiter.allow(
            _key("request", network.ip), limit=REQUESTS_PER_IP, window=auth.HOUR
        ):
            return True
        log.warning("otp.request_limited")
        return False

    def _send(self, email: str, network: Network, purpose: CodeEmail) -> None:
        with self._ports.unit_of_work() as uow:
            code_id = self._issue(uow, email, network, purpose)
        if code_id is not None:
            log.info("otp.requested", otp_code_id=str(code_id))

    def _issue(
        self, uow: UnitOfWork, email: str, network: Network, purpose: CodeEmail
    ) -> uuid.UUID | None:
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
        if uow.outbox.add_otp_email(
            code_id, sealed=sealed, minutes=minutes, at=at, purpose=purpose
        ):
            uow.jobs.dispatch_outbox()
        target = (AuditTarget.OTP_CODE, code_id)
        uow.audit.record(AuditEntry(AuditAction.CODE_REQUESTED, at, target=target, network=network))
        return code_id

    def _verify(self, uow: UnitOfWork, email: str, code: str, network: Network) -> SignedIn | None:
        if not self.check_code(uow, email, code, network):
            return None
        user_id = uow.accounts.user_for(email, at=self._ports.clock.now())
        return self._open_session(uow, user_id, network)

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
