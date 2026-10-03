"""Who may get a sign-in code (ADR-0009)."""

import pytest

from serpsense.domain.auth import SignupPolicy

pytestmark = pytest.mark.unit


def test_invite_mode_lets_in_only_the_listed_addresses_and_domains() -> None:
    assert SignupPolicy(invite_only=False).allows("anyone@example.com")
    invite = SignupPolicy(True, frozenset({"judge@example.com"}), frozenset({"serpsense.in"}))
    assert invite.allows("Judge@Example.com") and invite.allows("me@SerpSense.in")
    assert not invite.allows("someone@example.com")
    assert not invite.allows("me@evil-serpsense.in")
