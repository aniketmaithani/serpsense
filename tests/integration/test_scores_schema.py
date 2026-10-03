"""Scoring reference data on real Postgres: s1 is the domain's numbers, and every version holds
together."""

import pytest
from sqlalchemy import Connection, insert, text, update
from sqlalchemy.exc import DBAPIError

from serpsense.domain.enums import CrisisLevel
from serpsense.domain.scoring import crisis as crisis_rules
from serpsense.domain.scoring import surfaces as surface_rules
from tests.integration.db_helpers import table

pytestmark = pytest.mark.integration

WEIGHTS = table("scoring_weights")


def test_the_s1_reference_rows_are_the_domains_numbers(conn: Connection) -> None:
    weights = conn.execute(text("SELECT kind, component, weight_bp FROM scoring_weights")).all()
    health = {c: w for k, c, w in weights if k == "health"}
    crisis = {c: w for k, c, w in weights if k == "crisis"}
    assert health == {s.value: w for s, w in surface_rules.HEALTH_WEIGHTS_BP.items()}
    assert crisis == {c.value: w for c, w in crisis_rules.CRISIS_WEIGHTS_BP.items()}
    floors = dict(conn.execute(text("SELECT level, min_score FROM crisis_level_thresholds")).all())
    assert floors == {level.value: floor for level, floor in crisis_rules.LEVEL_FLOORS}
    warm_up = conn.execute(text("SELECT warm_up_scans FROM scoring_versions")).scalar_one()
    assert warm_up == crisis_rules.WARM_UP_SCANS


def test_every_version_has_crisis_weights_summing_to_10000_and_rising_floors_from_0(
    conn: Connection,
) -> None:
    for version in conn.execute(text("SELECT version FROM scoring_versions")).scalars():
        crisis = text(
            "SELECT sum(weight_bp) FROM scoring_weights WHERE version = :v AND kind = 'crisis'"
        )
        assert conn.execute(crisis, {"v": version}).scalar_one() == 10000
        floors = dict(
            conn.execute(
                text("SELECT level, min_score FROM crisis_level_thresholds WHERE version = :v"),
                {"v": version},
            ).all()
        )
        by_rank = [floors[level.value] for level in sorted(CrisisLevel, key=lambda lvl: lvl.rank)]
        assert by_rank[0] == 0 and by_rank == sorted(set(by_rank))


@pytest.mark.parametrize(
    ("kind", "component", "weight_bp", "constraint"),
    [
        ("health", "velocity", 100, "component_of_kind"),
        ("crisis", "news", 100, "component_of_kind"),
        ("health", "youtube", 0, "weight_bp_range"),  # a zero weight would divide by zero
    ],
)
def test_a_weight_belongs_to_its_kind_and_is_never_zero(
    conn: Connection, kind: str, component: str, weight_bp: int, constraint: str
) -> None:
    row = {"version": "s1", "kind": kind, "component": component, "weight_bp": weight_bp}
    with pytest.raises(DBAPIError, match=constraint), conn.begin_nested():
        conn.execute(insert(WEIGHTS).values(**row))


def test_two_levels_never_share_a_floor_and_reference_rows_never_change(conn: Connection) -> None:
    conn.execute(text("INSERT INTO scoring_versions VALUES ('s9', 3, now())"))
    thresholds = table("crisis_level_thresholds")
    conn.execute(insert(thresholds).values(version="s9", level="low", min_score=0))
    with pytest.raises(DBAPIError, match="version_min_score"), conn.begin_nested():
        conn.execute(insert(thresholds).values(version="s9", level="medium", min_score=0))
    with pytest.raises(DBAPIError, match="append-only"), conn.begin_nested():
        conn.execute(update(WEIGHTS).values(weight_bp=1))
