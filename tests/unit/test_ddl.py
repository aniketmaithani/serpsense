import pytest

from serpsense.adapters.db.ddl import (
    CREATE_FORBID_MUTATION_FUNCTION,
    DROP_FORBID_MUTATION_FUNCTION,
    FORBID_MUTATION_FUNCTION,
    append_only_triggers,
    drop_append_only_triggers,
)

pytestmark = pytest.mark.unit


def test_append_only_covers_row_mutations_and_truncate() -> None:
    row, truncate = append_only_triggers("ledger")
    assert row.startswith("CREATE TRIGGER trg_ledger_append_only BEFORE UPDATE OR DELETE ON ledger")
    assert "FOR EACH ROW" in row
    assert truncate.startswith("CREATE TRIGGER trg_ledger_append_only_truncate BEFORE TRUNCATE")
    assert "FOR EACH STATEMENT" in truncate
    assert all(f"EXECUTE FUNCTION {FORBID_MUTATION_FUNCTION}()" in s for s in (row, truncate))


def test_drop_removes_both_triggers() -> None:
    assert drop_append_only_triggers("ledger") == (
        "DROP TRIGGER trg_ledger_append_only_truncate ON ledger",
        "DROP TRIGGER trg_ledger_append_only ON ledger",
    )


def test_function_raises_restrict_violation() -> None:
    assert "ERRCODE = 'restrict_violation'" in CREATE_FORBID_MUTATION_FUNCTION
    assert f"DROP FUNCTION {FORBID_MUTATION_FUNCTION}()" == DROP_FORBID_MUTATION_FUNCTION
