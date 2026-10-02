"""Applied migrations depend on this exact DDL; changing it must fail a fast unit test."""

import pytest

from serpsense.adapters.db.ddl import (
    CREATE_FORBID_MUTATION_FUNCTION,
    DROP_FORBID_MUTATION_FUNCTION,
    append_only_triggers,
    drop_append_only_triggers,
)

pytestmark = pytest.mark.unit


def test_append_only_triggers_exact_ddl() -> None:
    assert append_only_triggers("t") == (
        "CREATE TRIGGER trg_t_append_only BEFORE UPDATE OR DELETE ON t "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_forbid_mutation()",
        "CREATE TRIGGER trg_t_append_only_truncate BEFORE TRUNCATE ON t "
        "FOR EACH STATEMENT EXECUTE FUNCTION serpsense_forbid_mutation()",
    )


def test_drop_append_only_triggers_exact_ddl() -> None:
    assert drop_append_only_triggers("t") == (
        "DROP TRIGGER trg_t_append_only_truncate ON t",
        "DROP TRIGGER trg_t_append_only ON t",
    )


def test_forbid_mutation_function_exact_ddl() -> None:
    assert CREATE_FORBID_MUTATION_FUNCTION == (
        "\nCREATE FUNCTION serpsense_forbid_mutation() RETURNS trigger\n"
        "LANGUAGE plpgsql AS $$\n"
        "BEGIN\n"
        "    RAISE EXCEPTION 'table % is append-only (% rejected)', TG_TABLE_NAME, TG_OP\n"
        "        USING ERRCODE = 'restrict_violation';\n"
        "END;\n"
        "$$\n"
    )
    assert DROP_FORBID_MUTATION_FUNCTION == "DROP FUNCTION serpsense_forbid_mutation()"
