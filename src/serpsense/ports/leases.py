"""Port for holding a named lease while slow work runs, so one person can't start the same work
twice at once (a draft per user, ADR-0008). A lease ends with the block, or with the process
that held it, so a crash never leaves one stuck."""

from contextlib import AbstractContextManager
from typing import Protocol


class Leases(Protocol):
    def hold(self, name: str) -> AbstractContextManager[bool]:
        """The block holds `name` when it gets True; False means someone else holds it, and the
        block must not do the work."""
        ...
