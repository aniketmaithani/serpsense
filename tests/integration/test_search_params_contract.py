"""The Python no-key rule agrees with the database CHECK, so the client never makes a billed
call whose ledger row Postgres would then reject (ADR-0007, data-model §5)."""

import json

import pytest
from sqlalchemy import Connection, text

from serpsense.adapters.db.ddl import no_api_key_check
from serpsense.domain.search import contains_api_key

pytestmark = pytest.mark.integration

SAMPLES: list[object] = [
    {"q": "ola"},
    {"q": "ola api_key leak"},
    {"q": "ola", "items": ["api_key", 3, None, True]},
    {"api_key": None},
    {"a": [{"b": {"api_key": "k"}}]},
    {"next": "https://serpapi.com/x?api_key=k"},
    {"https://serpapi.com/x?api_key=k": 1},
    {"q": "ओला api_key"},
    [[{"api_key": 1}]],
    {"api_keys": "k", "my_api_key": "k"},
    {"q": "\x1api_key=x"},
    {"q": "tab\there", "nl": "line\nbreak"},
    {"\x1api_key=": 1},  # a control character in a key
]


@pytest.mark.parametrize("sample", SAMPLES)
def test_python_rule_matches_the_check(conn: Connection, sample: object) -> None:
    check = no_api_key_check("CAST(:doc AS jsonb)")
    accepted = conn.execute(text(f"SELECT {check}"), {"doc": json.dumps(sample)}).scalar_one()
    assert accepted == (not contains_api_key(sample))
