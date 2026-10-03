"""SMTP mailer (smtplib): Mailpit in development, the provider in production (ADR-0010).

Errors are sorted for the outbox. A 5xx reply rejects the email for good; a 4xx reply (a
greylisted recipient too), a timeout or a connection that can't be made is worth retrying, and so
is a refusal of our own credentials or sender address, a settings mistake that can be fixed. An
email that can't be built (a line break in a header, a recipient that isn't one bare address)
is never sent. Error codes carry no address or server text.
"""

import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr

from serpsense.ports.mailer import Email, MailFailed

TIMEOUT_SECONDS = 20


@dataclass(frozen=True, kw_only=True)
class SmtpSettings:
    host: str
    port: int
    sender: str
    user: str = ""
    password: str = field(default="", repr=False)
    starttls: bool = False


class SmtpMailer:
    def __init__(self, settings: SmtpSettings) -> None:
        self._settings = settings

    def send(self, email: Email) -> None:
        message = _message(self._settings.sender, email)
        try:
            self._deliver(message, email.to)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [code for code, _ in exc.recipients.values()]
            greylisted = bool(codes) and all(400 <= code < 500 for code in codes)
            raise MailFailed("smtp.recipient_refused", retryable=greylisted) from exc
        except smtplib.SMTPAuthenticationError as exc:
            raise MailFailed("smtp.auth_failed", retryable=True) from exc
        except smtplib.SMTPSenderRefused as exc:
            raise MailFailed("smtp.sender_refused", retryable=True) from exc
        except (smtplib.SMTPConnectError, smtplib.SMTPHeloError) as exc:
            raise MailFailed("smtp.unavailable", retryable=True) from exc
        except smtplib.SMTPResponseException as exc:
            permanent = 500 <= exc.smtp_code < 600
            code = "smtp.rejected" if permanent else "smtp.deferred"
            raise MailFailed(code, retryable=not permanent) from exc
        except TimeoutError as exc:
            raise MailFailed("smtp.timeout", retryable=True) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise MailFailed("smtp.unavailable", retryable=True) from exc

    def _deliver(self, message: EmailMessage, recipient: str) -> None:
        settings = self._settings
        with smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT_SECONDS) as smtp:
            if settings.starttls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.user:
                smtp.login(settings.user, settings.password)
            smtp.send_message(message, to_addrs=[recipient])  # never the header's list


def _message(sender: str, email: Email) -> EmailMessage:
    name, address = parseaddr(email.to)
    if name or address != email.to or "," in email.to:
        raise MailFailed("smtp.invalid_recipient", retryable=False)
    message = EmailMessage()
    try:
        message["From"], message["To"], message["Subject"] = sender, email.to, email.subject
    except ValueError as exc:  # a line break in a header
        raise MailFailed("smtp.invalid_message", retryable=False) from exc
    message.set_content(email.text)
    return message
