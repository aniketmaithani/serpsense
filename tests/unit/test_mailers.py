"""The mailers: SMTP sorts its errors for the outbox, the console one stays out of production."""

import io
import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from serpsense.adapters.mail import smtp as smtp_module
from serpsense.adapters.mail.console import ConsoleMailer
from serpsense.adapters.mail.fake import FakeMailer
from serpsense.adapters.mail.smtp import SmtpMailer, SmtpSettings
from serpsense.ports.mailer import Email, MailFailed

pytestmark = pytest.mark.unit

EMAIL = Email("owner@example.com", "Ola: crisis level rose to high", "The crisis score is 74.")
SETTINGS = SmtpSettings(host="mailpit", port=1025, sender="SerpSense <no-reply@serpsense.local>")


class Server:
    """Stands in for smtplib.SMTP: records what it's asked, or raises what it's given."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[object, ...]] = []
        self.sent: list[EmailMessage] = []

    def __call__(self, host: str, port: int, *, timeout: float) -> "Server":
        self.calls.append(("connect", host, port))
        return self

    def __enter__(self) -> "Server":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, **kwargs: Any) -> None:
        self.calls.append(("starttls",))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def send_message(self, message: EmailMessage, *, to_addrs: list[str]) -> None:
        if self.failure is not None:
            raise self.failure
        self.calls.append(("send", *to_addrs))
        self.sent.append(message)


def test_smtp_sends_a_plain_text_email(monkeypatch: pytest.MonkeyPatch) -> None:
    server = Server()
    monkeypatch.setattr(smtp_module.smtplib, "SMTP", server)
    secure = SmtpSettings(
        host="smtp.example.com",
        port=587,
        sender=SETTINGS.sender,
        user="u",
        password="pw",
        starttls=True,
    )
    SmtpMailer(secure).send(EMAIL)
    assert [call[0] for call in server.calls] == ["connect", "starttls", "login", "send"]
    assert server.calls[-1] == ("send", EMAIL.to)  # to that one address, whatever the header says
    assert "pw" not in repr(secure)
    (message,) = server.sent
    assert (message["To"], message["Subject"]) == (EMAIL.to, EMAIL.subject)
    assert message.get_content().strip() == EMAIL.text


@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        (smtplib.SMTPDataError(550, b"no such user"), "smtp.rejected", False),
        (smtplib.SMTPDataError(451, b"try later"), "smtp.deferred", True),
        (
            smtplib.SMTPRecipientsRefused({EMAIL.to: (550, b"no such user")}),
            "smtp.recipient_refused",
            False,
        ),
        (
            smtplib.SMTPRecipientsRefused({EMAIL.to: (450, b"greylisted")}),
            "smtp.recipient_refused",
            True,
        ),
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "smtp.auth_failed", True),
        (
            smtplib.SMTPSenderRefused(553, b"not allowed", "no-reply@x.in"),
            "smtp.sender_refused",
            True,
        ),
        (smtplib.SMTPHeloError(501, b"bad helo"), "smtp.unavailable", True),
        (TimeoutError(), "smtp.timeout", True),
        (ConnectionRefusedError(), "smtp.unavailable", True),
        (smtplib.SMTPServerDisconnected(), "smtp.unavailable", True),
    ],
)
def test_smtp_sorts_its_errors(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException, code: str, retryable: bool
) -> None:
    monkeypatch.setattr(smtp_module.smtplib, "SMTP", Server(failure))
    with pytest.raises(MailFailed) as exc:
        SmtpMailer(SETTINGS).send(EMAIL)
    assert (exc.value.code, exc.value.retryable) == (code, retryable)
    assert EMAIL.to not in str(exc.value)  # nothing personal in what gets logged


@pytest.mark.parametrize(
    ("email", "code"),
    [
        (Email("a@x.in, b@y.in", "Hi", "Text"), "smtp.invalid_recipient"),
        (Email("Owner <a@x.in>", "Hi", "Text"), "smtp.invalid_recipient"),
        (Email("a@x.in", "Hi\nBcc: b@y.in", "Text"), "smtp.invalid_message"),
    ],
)
def test_an_email_that_cant_be_built_is_never_sent(
    monkeypatch: pytest.MonkeyPatch, email: Email, code: str
) -> None:
    server = Server()
    monkeypatch.setattr(smtp_module.smtplib, "SMTP", server)
    with pytest.raises(MailFailed) as exc:
        SmtpMailer(SETTINGS).send(email)
    assert (exc.value.code, exc.value.retryable, server.sent) == (code, False, [])


def test_the_console_mailer_writes_out_and_refuses_production() -> None:
    out = io.StringIO()
    ConsoleMailer(production=False, out=out).send(EMAIL)
    assert "Subject: Ola: crisis level rose to high" in out.getvalue()
    with pytest.raises(RuntimeError, match="development"):
        ConsoleMailer(production=True)


def test_the_fake_mailer_fails_on_cue_then_sends() -> None:
    mailer = FakeMailer(MailFailed("smtp.timeout", retryable=True))
    with pytest.raises(MailFailed):
        mailer.send(EMAIL)
    mailer.send(EMAIL)
    assert mailer.sent == [EMAIL]
