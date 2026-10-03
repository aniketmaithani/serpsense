"""Email addresses as accounts take them (ADR-0009, ADR-0013)."""

import pytest

from serpsense.ports.accounts import InvalidEmail, email_address

pytestmark = pytest.mark.unit


def test_an_address_is_trimmed_and_kept_as_typed() -> None:
    assert email_address("  Demo@Example.com \n") == "Demo@Example.com"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-an-address",
        "two@@example.com",
        "no-domain@localhost",
        "a b@example.com",
        "nul\x00@example.com",
        "a@x.in, b@y.in",  # a list SMTP would fan out to
        "Owner <a@x.in>",
        "a;b@x.in",
        "x" * 250 + "@a.in",  # longer than 254
        "deleted+0b6f@serpsense.invalid",  # a deleted account's pseudonym
        "DELETED+0b6f@SERPSENSE.INVALID",
    ],
)
def test_anything_else_is_refused(raw: str) -> None:
    with pytest.raises(InvalidEmail):
        email_address(raw)
