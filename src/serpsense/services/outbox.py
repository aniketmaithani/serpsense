"""Send the outbox's due emails (ADR-0010).

Each message is claimed in a unit of work of its own, and its row stays locked through rendering,
sending and recording the attempt, until that unit of work commits: there is no "sending" state,
so a worker that dies mid-send just releases the lock. This is the one place an outside call (to
the mail server) happens inside a unit of work, as ADR-0010 decides; delivery is at least once.
A message that can't be rendered (an unknown template, missing fields) fails for good rather
than blocking the emails behind it.
"""

from collections.abc import Callable, Mapping

from serpsense.domain.enums import OutboxOutcome, OutboxStatus
from serpsense.observability import get_logger
from serpsense.ports.clock import Clock
from serpsense.ports.mailer import Email, Mailer, MailFailed
from serpsense.ports.outbox import DueEmail
from serpsense.ports.unit_of_work import UnitOfWorkFactory

log = get_logger(__name__)

BATCH = 25  # messages per run; Beat runs the dispatcher every 15 seconds
FOOTER = "\n\n--\nSerpSense watches how your brand looks on Google. This email is automated."


class UnknownTemplate(LookupError):
    pass


def _alert(data: Mapping[str, str]) -> tuple[str, str]:
    return data["title"], data["body"]


TEMPLATES: Mapping[str, Callable[[Mapping[str, str]], tuple[str, str]]] = {"alert": _alert}


def render(due: DueEmail) -> Email:
    """The email a message stands for; plain text, so nothing needs escaping."""
    template = TEMPLATES.get(due.template)
    if template is None:
        raise UnknownTemplate(due.template)
    subject, body = template(due.data)
    return Email(due.recipient, subject, body + FOOTER)


class OutboxDispatcher:
    def __init__(self, unit_of_work: UnitOfWorkFactory, mailer: Mailer, clock: Clock) -> None:
        self._unit_of_work, self._mailer, self._clock = unit_of_work, mailer, clock

    def dispatch(self, limit: int = BATCH) -> int:
        """Send up to `limit` due messages; how many were sent."""
        sent = 0
        for _ in range(limit):
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
        try:
            self._mailer.send(render(due))
        except UnknownTemplate:
            return OutboxOutcome.PERMANENT_ERROR, "outbox.unknown_template"
        except KeyError:  # template data missing a field the template needs
            return OutboxOutcome.PERMANENT_ERROR, "outbox.render_failed"
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
    elif status is OutboxStatus.DEAD:
        log.error("outbox.dead", error_code=code, **fields)
    else:
        log.warning("outbox.send_failed", error_code=code, **fields)
