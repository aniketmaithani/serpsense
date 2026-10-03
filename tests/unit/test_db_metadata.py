"""Schema conventions that must hold for every table (AGENTS.md §4, data-model.md conventions)."""

import pytest
from sqlalchemy import DateTime, ForeignKeyConstraint, Table

from serpsense.adapters.db import models  # noqa: F401  (registers tables)
from serpsense.adapters.db.base import Base

pytestmark = pytest.mark.unit

TABLES = list(Base.metadata.sorted_tables)


def test_metadata_has_expected_tables() -> None:
    assert {t.name for t in TABLES} >= {
        "users",
        "otp_codes",
        "otp_verify_attempts",
        "sessions",
        "user_search_budgets",
        "user_llm_budgets",
        "brands",
        "brand_competitors",
        "brand_aliases",
        "brand_languages",
        "brand_watch_terms",
        "brand_apps",
        "brand_locations",
        "user_llm_profile_versions",
        "user_search_default_versions",
        "brand_search_settings_versions",
        "brand_schedule_versions",
        "scans",
        "scan_status_transitions",
        "scan_surface_results",
        "serp_calls",
        "raw_responses",
        "mentions",
        "mention_observations",
        "mention_revisions",
        "app_rating_observations",
        "trends_observations",
        "llm_calls",
        "enrichments",
        "narratives",
        "narrative_assignments",
    }


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_every_constraint_and_index_is_named(table: Table) -> None:
    for item in [*table.constraints, *table.indexes]:
        assert item.name, f"{table.name}: unnamed {type(item).__name__}"


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_every_foreign_key_restricts_deletes(table: Table) -> None:
    for constraint in table.constraints:
        if isinstance(constraint, ForeignKeyConstraint):
            assert constraint.ondelete == "RESTRICT", f"{constraint.name} must be RESTRICT"


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_timestamps_are_timezone_aware(table: Table) -> None:
    for column in table.columns:
        if isinstance(column.type, DateTime):
            assert column.type.timezone, f"{table.name}.{column.name} must be timestamptz"
