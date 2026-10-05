"""Who may get a sign-in code (ADR-0009, ADR-0015)."""

import pytest

from serpsense.domain.auth import SignupPolicy
from serpsense.domain.enums import SignupMode

pytestmark = pytest.mark.unit


def test_invite_mode_lets_in_only_the_listed_addresses_and_domains() -> None:
    assert SignupPolicy(invite_only=False).allows("anyone@example.com", None)
    invite = SignupPolicy(True, frozenset({"judge@example.com"}), frozenset({"serpsense.in"}))
    assert invite.allows("Judge@Example.com", None) and invite.allows("me@SerpSense.in", None)
    assert not invite.allows("someone@example.com", None)
    assert not invite.allows("me@evil-serpsense.in", None)


def test_the_operators_switch_overrides_the_environments_mode() -> None:
    invite = SignupPolicy(True, frozenset({"judge@example.com"}))
    open_ = SignupPolicy(invite_only=False)
    assert invite.allows("anyone@example.com", SignupMode.OPEN)  # ADR-0015: opened
    assert not open_.allows("anyone@example.com", SignupMode.INVITE)  # closed
    assert invite.allows("judge@example.com", SignupMode.INVITE)  # the lists still count
    assert invite.invite_only_under(None) and not open_.invite_only_under(None)  # no switch yet
