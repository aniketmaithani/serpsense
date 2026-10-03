"""What a model call costs, in integer micros of US dollars (ADR-0008, AGENTS §3: no floats).

Prices are micros per million tokens, so a call costs the sum of tokens times price over a
million, rounded up to a whole micro so many small calls never add up to less than they cost.
A call is priced per hop: a refusal fallback can run the request on several models, each
billed at its own rates, so every model a fallback may serve is priced here, by exact id.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from serpsense.domain.llm_capabilities import HAIKU, OPUS, SONNET

CURRENCY = "USD"
MILLION = 1_000_000


@dataclass(frozen=True)
class Prices:
    """Micros per million tokens."""

    input: int
    output: int
    cache_read: int
    cache_write: int  # 1.25x input, the five-minute cache


@dataclass(frozen=True)
class TokenUsage:
    input: int  # uncached input
    output: int  # includes thinking
    cache_read: int
    cache_write: int

    def __post_init__(self) -> None:
        if min(self.input, self.output, self.cache_read, self.cache_write) < 0:
            raise ValueError("token counts can't be negative")


PRICES: Mapping[str, Prices] = {
    OPUS: Prices(input=4_000_000, output=20_000_000, cache_read=200_000, cache_write=5_000_000),
    SONNET: Prices(input=2_000_000, output=10_000_000, cache_read=200_000, cache_write=2_500_000),
    HAIKU: Prices(input=1_000_000, output=5_000_000, cache_read=100_000, cache_write=1_250_000),
    # The dated id the API may return for Haiku 4.5, and the models a refusal fallback serves.
    "claude-haiku-4-5-20251001": Prices(1_000_000, 5_000_000, 100_000, 1_250_000),
    "claude-opus-5": Prices(5_000_000, 25_000_000, 500_000, 6_250_000),
    "claude-opus-4-8": Prices(5_000_000, 25_000_000, 500_000, 6_250_000),
    "claude-sonnet-5": Prices(2_000_000, 10_000_000, 200_000, 2_500_000),
}


@dataclass(frozen=True)
class Hop:
    """One model's part of a call: the one that answered, or one that declined first."""

    model: str
    usage: TokenUsage


def total(hops: Iterable[Hop]) -> TokenUsage:
    hops = list(hops)
    return TokenUsage(
        input=sum(h.usage.input for h in hops),
        output=sum(h.usage.output for h in hops),
        cache_read=sum(h.usage.cache_read for h in hops),
        cache_write=sum(h.usage.cache_write for h in hops),
    )


class UnknownModel(LookupError):
    """A served model with no price: the call can't be accounted for."""


def cost_micros(model: str, usage: TokenUsage) -> int:
    prices = PRICES.get(model)
    if prices is None:
        raise UnknownModel(model)
    total = (
        usage.input * prices.input
        + usage.output * prices.output
        + usage.cache_read * prices.cache_read
        + usage.cache_write * prices.cache_write
    )
    return -(-total // MILLION)  # rounded up to a whole micro
