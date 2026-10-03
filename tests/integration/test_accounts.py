"""User accounts by email, on real Postgres."""

import pytest
from sqlalchemy import Connection

from serpsense.adapters.db.accounts import SqlAccounts
from serpsense.ports.accounts import InvalidEmail
from tests.integration.db_helpers import NOW

pytestmark = pytest.mark.integration


def test_a_user_is_found_by_email_ignoring_case_or_created(conn: Connection) -> None:
    accounts = SqlAccounts(conn)
    owner = accounts.user_for(" Demo@Example.com ", at=NOW)
    assert accounts.user_for("demo@example.com", at=NOW) == owner
    assert accounts.user_for("other@example.com", at=NOW) != owner
    for refused in ("not-an-address", "deleted+1@serpsense.invalid"):
        with pytest.raises(InvalidEmail):
            accounts.user_for(refused, at=NOW)
