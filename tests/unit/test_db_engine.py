"""The database engine keeps bound values out of errors (AGENTS §8, #99)."""

import pytest

from serpsense.adapters.db.engine import create_db_engine

pytestmark = pytest.mark.unit


def test_an_error_never_shows_the_values_it_was_given() -> None:
    assert create_db_engine("postgresql+psycopg://t:t@localhost:5432/t").hide_parameters
