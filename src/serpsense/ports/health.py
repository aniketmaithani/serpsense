"""Port for dependency health checks used by /readyz."""

from typing import Protocol


class HealthCheck(Protocol):
    @property
    def name(self) -> str: ...

    def check(self) -> bool:
        """Return True when the dependency is reachable. Must not raise."""
        ...
