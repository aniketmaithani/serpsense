"""The score store on real Postgres: a scan's scores, written once."""

from datetime import timedelta
from types import MappingProxyType

import pytest
from sqlalchemy import Connection, func, select, text

from serpsense.adapters.db.score_store import SqlScoreStore
from serpsense.domain.enums import CrisisComponent, Surface
from serpsense.domain.scoring.scan import ScanScores
from serpsense.ports.score_store import ScoreStore
from tests.integration.db_helpers import NOW, add_owned_brand, add_scan, table

pytestmark = pytest.mark.integration

QUIET = MappingProxyType({c: 0 for c in CrisisComponent})
VIEW = text("SELECT health, crisis, crisis_level FROM v_scan_scores WHERE scan_id = :id")


@pytest.fixture
def store(conn: Connection) -> ScoreStore:
    return SqlScoreStore(conn)


def count(conn: Connection, name: str) -> int:
    return conn.execute(select(func.count()).select_from(table(name))).scalar_one()


def test_scores_are_recorded_once_and_the_view_reads_them(
    conn: Connection, store: ScoreStore
) -> None:
    scan_id = add_scan(conn, add_owned_brand(conn), status="running")
    spike = QUIET | {CrisisComponent.PRESS: 80}
    scores = ScanScores(MappingProxyType({Surface.NEWS: 50, Surface.PLAY: 70}), spike)

    assert store.record(scan_id, scores, version="s1", at=NOW) is True
    again = ScanScores(MappingProxyType({Surface.NEWS: 0}), QUIET)
    later = NOW + timedelta(hours=1)
    assert store.record(scan_id, again, version="s1", at=later) is False  # the first stay
    assert tuple(conn.execute(VIEW, {"id": scan_id}).one()) == (
        60,
        8,
        None,
    )  # equal weights; warming up
    assert (count(conn, "surface_scores"), count(conn, "crisis_components")) == (2, 5)


def test_a_scan_that_showed_nothing_still_records_its_components(
    conn: Connection, store: ScoreStore
) -> None:
    scan_id = add_scan(conn, add_owned_brand(conn), status="running")
    assert store.record(scan_id, ScanScores(MappingProxyType({}), QUIET), version="s1", at=NOW)
    assert tuple(conn.execute(VIEW, {"id": scan_id}).one()) == (None, 0, None)
    assert (count(conn, "surface_scores"), count(conn, "crisis_components")) == (0, 5)
