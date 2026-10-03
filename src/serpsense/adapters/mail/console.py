"""Console mailer for local development: writes each email to standard output instead of
sending it. It may show an OTP, so it refuses to exist in production (ADR-0009)."""

import sys
from typing import TextIO

from serpsense.ports.mailer import Email


class ConsoleMailer:
    def __init__(self, *, production: bool, out: TextIO = sys.stdout) -> None:
        if production:
            raise RuntimeError("the console mailer is for development only")
        self._out = out

    def send(self, email: Email) -> None:
        self._out.write(f"To: {email.to}\nSubject: {email.subject}\n\n{email.text}\n\n")
        self._out.flush()
