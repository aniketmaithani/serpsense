"""Fake mailer for tests: keeps what it was asked to send, and can fail on cue."""

from serpsense.ports.mailer import Email, MailFailed


class FakeMailer:
    def __init__(self, *failures: MailFailed) -> None:
        self.sent: list[Email] = []
        self._failures = list(failures)  # raised in turn, before anything is sent

    def send(self, email: Email) -> None:
        if self._failures:
            raise self._failures.pop(0)
        self.sent.append(email)
