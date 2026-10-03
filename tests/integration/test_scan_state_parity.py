"""The domain's transition table and the migrated CHECK agree on every pair (data-model §4)."""

import itertools
import re

import pytest
from sqlalchemy import Connection, text

from serpsense.domain.enums import ScanStatus
from serpsense.domain.scan_state import ALLOWED

pytestmark = pytest.mark.integration

# The constraint as the migrations installed it, not as the ORM model declares it.
DEFINITION = text(
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conname = 'ck_scan_status_transitions_allowed_transition'"
)
PAIRS = list(itertools.product([None, *ScanStatus], ScanStatus))


def test_code_and_database_agree_on_every_pair(conn: Connection) -> None:
    check = conn.execute(DEFINITION).scalar_one()
    sql = re.sub(r"\bfrom_status\b", "CAST(:f AS scan_status)", check.removeprefix("CHECK "))
    sql = re.sub(r"\bto_status\b", "CAST(:t AS scan_status)", sql)

    def database_allows(from_status: ScanStatus | None, to_status: ScanStatus) -> bool:
        result = conn.execute(text(f"SELECT {sql}"), {"f": from_status, "t": to_status})
        return result.scalar_one() is not False  # a CHECK passes on NULL too

    disagreements = [
        (start, to)
        for start, to in PAIRS
        if database_allows(start, to) is not (to in ALLOWED.get(start, frozenset()))
    ]
    assert len(PAIRS) == 42 and disagreements == []
