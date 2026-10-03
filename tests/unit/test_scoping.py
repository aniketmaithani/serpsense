"""The one owner check: every user-scoped query goes through it, or it isn't built."""

import pytest

from serpsense.adapters.db.scoping import OWNED, scoped

pytestmark = pytest.mark.unit


def test_a_scoped_query_reaches_its_rows_through_the_owner_check() -> None:
    query = scoped("SELECT name FROM brands WHERE id IN ({owned})")
    assert str(query).endswith(f"IN ({OWNED})") and "{owned}" not in str(query)
    assert ":user" in OWNED and "archived_at IS NULL" in OWNED


def test_a_query_without_the_owner_check_is_refused() -> None:
    with pytest.raises(ValueError, match="owned"):
        scoped("SELECT name FROM brands WHERE id = :brand")
