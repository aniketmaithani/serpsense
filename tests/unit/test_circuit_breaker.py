"""The circuit breaker opens only on five transient failures in a row (data-model §5)."""

import pytest

from serpsense.domain.circuit_breaker import is_open
from serpsense.domain.enums import SerpErrorCode

pytestmark = pytest.mark.unit

T, P = SerpErrorCode.TIMEOUT, SerpErrorCode.HTTP_4XX


@pytest.mark.parametrize(
    ("attempts", "open_"),
    [
        ([T] * 5, True),
        ([T, SerpErrorCode.NETWORK, SerpErrorCode.HTTP_429, SerpErrorCode.HTTP_5XX, T], True),
        ([T] * 5 + [None], True),  # only the newest five count
        ([T] * 4, False),  # too few attempts in the window
        ([T, T, None, T, T], False),  # a success breaks the run
        ([T, T, P, T, T], False),  # a permanent failure is the caller's fault, not SerpApi's
        ([None] + [T] * 5, False),  # the newest attempt succeeded
        ([], False),
    ],
)
def test_breaker(attempts: list[SerpErrorCode | None], open_: bool) -> None:
    assert is_open(attempts) is open_
