"""What model text may carry before a person reads it or an email sends it."""

import pytest

from serpsense.domain.model_text import has_contact, has_invisible

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    ["see https://x.example", "www.ola-help.in", "mail help@ola.example", "call +91 98765 43210"],
)
def test_links_addresses_and_phone_numbers_are_contacts(text: str) -> None:
    assert has_contact(text)


@pytest.mark.parametrize(
    "text", ["fares rose by 200 rupees", "crisis score 46 of 100", "[contact channel]"]
)
def test_numbers_and_the_placeholder_are_not(text: str) -> None:
    assert not has_contact(text)


def test_format_characters_are_invisible() -> None:
    zero_width, right_to_left = chr(0x200B), chr(0x202E)
    assert has_invisible(f"a{zero_width}b") and has_invisible(f"{right_to_left}evil")
    assert not has_invisible("₹200 ok")
