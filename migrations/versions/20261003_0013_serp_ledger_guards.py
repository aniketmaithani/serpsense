"""SerpApi ledger guards.

- A scan's calls belong to its brand's owner (trg_serp_calls_user_owns_scan, reported as
  ck_serp_calls_user_owns_scan); Preview calls have no scan and belong to the caller.
- Raw responses are kept only for successful calls (trg_raw_responses_call_succeeded, reported
  as ck_raw_responses_call_succeeded); the ledger is append-only, so an outcome never changes.
- Skipped calls never reached SerpApi, so they have no HTTP status and no latency
  (ck_serp_calls_skipped_not_sent).
- ix_raw_responses_created_at serves the retention job.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_OWNS_SCAN_FUNCTION = """
CREATE FUNCTION serpsense_serp_call_user_owns_scan() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    brand_owner uuid;
BEGIN
    SELECT b.owner_id INTO brand_owner
    FROM scans s JOIN brands b ON b.id = s.brand_id
    WHERE s.id = NEW.scan_id;
    -- "<>": NULL for a Preview call (no scan) or a missing scan (the foreign key reports it).
    IF brand_owner <> NEW.user_id THEN
        RAISE EXCEPTION 'a scan''s SerpApi calls belong to its brand''s owner'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_serp_calls_user_owns_scan';
    END IF;
    RETURN NEW;
END;
$$
"""

SKIPPED_NOT_SENT = (
    "outcome NOT IN ('skipped_budget', 'circuit_open') OR (http_status IS NULL AND latency_ms = 0)"
)

CALL_SUCCEEDED_FUNCTION = """
CREATE FUNCTION serpsense_raw_response_call_succeeded() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    call_outcome serp_call_outcome;
BEGIN
    SELECT outcome INTO call_outcome FROM serp_calls WHERE id = NEW.serp_call_id;
    -- "<>": a missing call yields NULL and is left to the foreign key to report.
    IF call_outcome <> 'succeeded' THEN
        RAISE EXCEPTION 'raw responses are kept only for successful calls'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_raw_responses_call_succeeded';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.execute(USER_OWNS_SCAN_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_serp_calls_user_owns_scan BEFORE INSERT ON serp_calls "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_serp_call_user_owns_scan()"
    )
    op.execute(CALL_SUCCEEDED_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_raw_responses_call_succeeded "
        "BEFORE INSERT OR UPDATE OF serp_call_id ON raw_responses "
        "FOR EACH ROW EXECUTE FUNCTION serpsense_raw_response_call_succeeded()"
    )
    op.create_index("ix_raw_responses_created_at", "raw_responses", ["created_at"])
    op.create_check_constraint(
        op.f("ck_serp_calls_skipped_not_sent"), "serp_calls", SKIPPED_NOT_SENT
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_serp_calls_skipped_not_sent"), "serp_calls", type_="check")
    op.drop_index("ix_raw_responses_created_at", table_name="raw_responses")
    op.execute("DROP TRIGGER trg_raw_responses_call_succeeded ON raw_responses")
    op.execute("DROP FUNCTION serpsense_raw_response_call_succeeded()")
    op.execute("DROP TRIGGER trg_serp_calls_user_owns_scan ON serp_calls")
    op.execute("DROP FUNCTION serpsense_serp_call_user_owns_scan()")
