"""Delete an account (ADR-0013).

Deleting takes a fresh code, emailed to the account's own address in an email that says what it
is for, entered with that address typed out in full: a session left open can't delete an
account, and neither can a slip of the mouse. A wrong address counts as a wrong guess at the
code, after the same work, so the answer and its timing don't say which was wrong. The code is
checked in the same unit of work as the deletion, so a code is never used up by a deletion that
then fails. In it, the user's pending emails are dropped, their queued scans skipped (a running
scan sees the deletion at its next stage and stops), and `Accounts.scrub` removes every address,
IP and user agent of theirs; the audit event holds counts, never an address. An email being sent
to the user right then is waited for; one that holds its row past the lock timeout makes the
deletion busy, with nothing changed and the code still good. What the user's searches and model
calls cost stays in the ledgers, under their pseudonymous id.
"""

import uuid
from dataclasses import asdict
from enum import StrEnum

from serpsense.domain.enums import (
    AuditAction,
    AuditTarget,
    CodeEmail,
    OutboxOutcome,
    ScanStatus,
    TransitionActor,
)
from serpsense.domain.scan_state import Transition, TransitionReason
from serpsense.observability import get_logger
from serpsense.ports.accounts import Scrubbed
from serpsense.ports.audit import AuditEntry, Network
from serpsense.ports.clock import Clock
from serpsense.ports.unit_of_work import Busy, UnitOfWork, UnitOfWorkFactory
from serpsense.services.auth import SignIn
from serpsense.services.sessions import CurrentUser

log = get_logger(__name__)


class Deletion(StrEnum):
    """How asking to delete an account ended."""

    DELETED = "deleted"
    REFUSED = "refused"  # a wrong code or address, no live code, or too many tries
    BUSY = "busy"  # an email to the user held its row too long: nothing changed, try again


class AccountDeletion:
    def __init__(self, unit_of_work: UnitOfWorkFactory, sign_in: SignIn, clock: Clock) -> None:
        self._unit_of_work, self._sign_in, self._clock = unit_of_work, sign_in, clock

    def send_code(self, user: CurrentUser, network: Network) -> None:
        """Email the user a code to confirm the deletion with, within every code's limits."""
        with self._unit_of_work() as uow:
            email = uow.accounts.email_of(user.user_id)
        if email is not None:
            self._sign_in.send_step_up_code(email, network, CodeEmail.DELETE_ACCOUNT)

    def delete(self, user: CurrentUser, code: str, typed_email: str, network: Network) -> Deletion:
        """Delete the account for the right code with its address typed out; nothing is
        deleted otherwise."""
        user_id = str(user.user_id)
        if not self._sign_in.may_verify(network):
            log.info("account.delete_refused", user_id=user_id)
            return Deletion.REFUSED
        try:
            with self._unit_of_work() as uow:
                scrubbed = self._delete(uow, user.user_id, code.strip(), typed_email, network)
        except Busy:
            log.info("account.delete_busy", user_id=user_id)
            return Deletion.BUSY
        if scrubbed is None:
            log.info("account.delete_refused", user_id=user_id)
            return Deletion.REFUSED
        log.info("account.deleted", user_id=user_id, **asdict(scrubbed))
        return Deletion.DELETED

    def _delete(
        self, uow: UnitOfWork, user_id: uuid.UUID, code: str, typed_email: str, network: Network
    ) -> Scrubbed | None:
        email = uow.accounts.email_of(user_id)
        if email is None:
            return None
        confirmed = typed_email.strip().casefold() == email.casefold()
        # The code is locked before the user's row, the order signing in takes them in (its new
        # session's foreign key share-locks the user's row); the other order deadlocks with a
        # sign-in using the same code. Holding the code also queues a second deletion, which
        # then finds the code used.
        if not self._sign_in.check_code(uow, email, code, network, confirmed=confirmed):
            return None
        if uow.accounts.lock(user_id) != email:  # the account as checked, locked for the scrub
            return None
        at = self._clock.now()
        for message_id in uow.outbox.pending_for(user_id, email):
            uow.outbox.record(message_id, OutboxOutcome.DROPPED, at=at)
        skip = Transition(
            ScanStatus.QUEUED,
            ScanStatus.SKIPPED,
            TransitionReason.ACCOUNT_DELETED,
            TransitionActor.USER,
            user_id,
        )
        for scan_id in uow.scans.queued_for_owner(user_id):
            uow.scans.move(scan_id, skip, at=at)
        scrubbed = uow.accounts.scrub(user_id, email, at=at)
        uow.audit.record(
            AuditEntry(  # no network row: the account is gone (ADR-0013)
                AuditAction.ACCOUNT_DELETED,
                at,
                actor_user_id=user_id,
                target=(AuditTarget.USER, user_id),
                details=asdict(scrubbed),
            )
        )
        return scrubbed
