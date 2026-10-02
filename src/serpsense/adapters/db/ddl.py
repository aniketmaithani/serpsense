"""DDL shared by migrations, so every append-only table is protected the same way.

Migrations are history: never change what an existing function here emits. Add a new
function (or a new migration) instead.
"""

FORBID_MUTATION_FUNCTION = "serpsense_forbid_mutation"

CREATE_FORBID_MUTATION_FUNCTION = f"""
CREATE FUNCTION {FORBID_MUTATION_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table % is append-only (% rejected)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$
"""
DROP_FORBID_MUTATION_FUNCTION = f"DROP FUNCTION {FORBID_MUTATION_FUNCTION}()"


def append_only_triggers(table: str) -> tuple[str, ...]:
    """Row-level UPDATE/DELETE and statement-level TRUNCATE are all rejected."""
    return (
        f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {FORBID_MUTATION_FUNCTION}()",
        f"CREATE TRIGGER trg_{table}_append_only_truncate BEFORE TRUNCATE ON {table} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {FORBID_MUTATION_FUNCTION}()",
    )


def drop_append_only_triggers(table: str) -> tuple[str, ...]:
    return (
        f"DROP TRIGGER trg_{table}_append_only_truncate ON {table}",
        f"DROP TRIGGER trg_{table}_append_only ON {table}",
    )
