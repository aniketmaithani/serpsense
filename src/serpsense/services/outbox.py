"""Send the outbox's due emails (ADR-0010).

Each message is claimed in a unit of work of its own, and its row stays locked through rendering,
sending and recording the attempt, until that unit of work commits: there is no "sending" state,
so a worker that dies mid-send just releases the lock. This is the one place an outside call (to
the mail server) happens inside a unit of work, as ADR-0010 decides; delivery is at least once.
A message that can't be rendered (an unknown template, missing fields, a seal no key opens) fails
for good rather than blocking the emails behind it. A sign-in code's email is dropped unsent once
the code expired, was used or was replaced; its sealed code is opened only to render it. A run
stops after a minute, well inside its task's limit and the session's idle timeout, and Beat
starts the next one.
"""

from collections.abc import Callable, Mapping
from datetime import timedelta

from serpsense.domain.enums import CodeEmail, OutboxOutcome, OutboxStatus
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.mailer import Email, Mailer, MailFailed
from serpsense.ports.outbox import DueEmail
from serpsense.ports.secret_box import SealBroken, SecretBox
from serpsense.ports.unit_of_work import UnitOfWorkFactory

log = get_logger(__name__)

BATCH = 25  # messages per run; Beat runs the dispatcher every 15 seconds
RUN_TIME = timedelta(minutes=1)  # no new claim after this; the task's hard limit is 2 minutes
FOOTER = "\n\n--\nSerpSense watches how your brand looks on Google. This email is automated."


def _alert(data: Mapping[str, str], secret: str | None) -> tuple[str, str]:
    return data["title"], data["body"]


def _otp(data: Mapping[str, str], secret: str | None) -> tuple[str, str]:
    if secret is None:
        raise KeyError("sealed")
    body = (
        f"Your sign-in code is {secret}\n\nIt works once, for {data['minutes']} minutes. If you "
        "didn't ask for it, ignore this email: nobody can sign in without the code."
    )
    return "Your SerpSense sign-in code", body


def _delete_code(data: Mapping[str, str], secret: str | None) -> tuple[str, str]:
    if secret is None:
        raise KeyError("sealed")
    body = (
        f"Your code to delete your SerpSense account is {secret}\n\nThis code deletes your "
        f"SerpSense account. It works once, for {data['minutes']} minutes. If you didn't ask "
        "for it, ignore it, and sign out everywhere (Settings, Account): someone may be signed "
        "in as you."
    )
    return "Your code to delete your SerpSense account", body


Template = Callable[[Mapping[str, str], str | None], tuple[str, str]]
TEMPLATES: Mapping[str, Template] = {
    "alert": _alert,
    CodeEmail.SIGN_IN: _otp,
    CodeEmail.DELETE_ACCOUNT: _delete_code,
}


class RenderFailed(ValueError):
    """The message can't become an email; `code` says why, and it is never retried."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def render(due: DueEmail, box: SecretBox) -> Email:
    """The email a message stands for (plain text, so nothing needs escaping); its sealed secret,
    if any, is opened here and nowhere else."""
    template = TEMPLATES.get(due.template)
    if template is None:
        raise RenderFailed("outbox.unknown_template")
    try:
        secret = box.open(due.sealed).decode() if due.sealed is not None else None
        subject, body = template(due.data, secret)
    except SealBroken as exc:  # sealed with a key that is gone
        raise RenderFailed("outbox.seal_broken") from exc
    except (KeyError, UnicodeDecodeError) as exc:  # missing fields, or a seal of garbage
        raise RenderFailed("outbox.render_failed") from exc
    return Email(due.recipient, subject, body + FOOTER)


class OutboxDispatcher:
    def __init__(
        self, unit_of_work: UnitOfWorkFactory, mailer: Mailer, box: SecretBox, clock: Clock
    ) -> None:
        self._unit_of_work, self._mailer, self._box, self._clock = unit_of_work, mailer, box, clock

    def dispatch(self, limit: int = BATCH) -> int:
        """Send up to `limit` due messages, for up to a minute; how many were sent."""
        sent, started = 0, self._clock.now()
        for _ in range(limit):
            if self._clock.now() - started >= RUN_TIME:
                break
            with self._unit_of_work() as uow:
                due = uow.outbox.claim_due(self._clock.now())
                if due is None:
                    break
                outcome, code = self._send(due)
                status = uow.outbox.record(
                    due.message_id, outcome, at=self._clock.now(), error_code=code
                )
            if outcome is OutboxOutcome.SENT:
                sent += 1
            _log(due, outcome, status, code)
        return sent

    def _send(self, due: DueEmail) -> tuple[OutboxOutcome, str | None]:
        if due.stale:
            return OutboxOutcome.DROPPED, None
        try:
            email = render(due, self._box)
        except RenderFailed as exc:
            return OutboxOutcome.PERMANENT_ERROR, exc.code
        try:
            self._mailer.send(email)
        except MailFailed as exc:
            failed = (
                OutboxOutcome.RETRYABLE_ERROR if exc.retryable else OutboxOutcome.PERMANENT_ERROR
            )
            return failed, exc.code
        return OutboxOutcome.SENT, None


def _log(due: DueEmail, outcome: OutboxOutcome, status: OutboxStatus, code: str | None) -> None:
    """Never the recipient or the content: the message id, kind and outcome only."""
    fields = {"message_id": str(due.message_id), "kind": due.kind}
    if outcome is OutboxOutcome.SENT:
        log.info("outbox.sent", **fields)
    elif outcome is OutboxOutcome.DROPPED:
        log.info("outbox.dropped", **fields)
    elif status is OutboxStatus.DEAD:
        log.error("outbox.dead", error_code=code, **fields)
    else:
        log.warning("outbox.send_failed", error_code=code, **fields)
