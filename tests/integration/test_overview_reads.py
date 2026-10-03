"""What the pages read, on real Postgres: one owner's brands only, the latest scan's surfaces and
labelled mentions, the score trend, alerts and competitors."""

import hashlib
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, insert

from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.adapters.db.overview import SqlOverview
from serpsense.domain.enums import (
    AlertRule,
    CrisisComponent,
    MentionSource,
    ScanStatus,
    Surface,
    SurfaceOutcome,
    Topic,
)
from serpsense.ports.enrichment_store import MentionLabel
from tests.integration.db_helpers import (
    NOW,
    add,
    add_app,
    add_brand,
    add_llm_call,
    add_mention,
    add_scan,
    add_user,
    table,
)
from tests.integration.test_scan_scores_view import scored

pytestmark = pytest.mark.integration

HOUR = timedelta(hours=1)
CALM = {c: 0 for c in CrisisComponent}


def overview(conn: Connection) -> SqlOverview:
    return SqlOverview(lambda: nullcontext(conn))


def mention(conn: Connection, brand_id: uuid.UUID, name: str, **values: Any) -> uuid.UUID:
    key = hashlib.sha256(name.encode()).hexdigest()
    row = {"identity_key": key, "url": f"https://n.in/{name}", "text": name.title(), **values}
    return add_mention(conn, brand_id, **row)


def label(
    conn: Connection,
    owner: uuid.UUID,
    mention_id: uuid.UUID,
    sentiment: int,
    *,
    about: bool = True,
    revision: int = 1,
    severity: int = 40,
    prompt: str = "label_mentions/v1",
) -> None:
    reason = "Late drivers." if sentiment < 0 else "Fine."
    complaint = sentiment < 0
    new = MentionLabel(
        mention_id, revision, sentiment, severity, Topic.RELIABILITY, complaint, about, reason
    )
    at = NOW if prompt == "label_mentions/v1" else NOW + HOUR
    call = add_llm_call(conn, owner, prompt_version=prompt, created_at=at)
    SqlEnrichmentStore(conn).record([new], prompt_version=prompt, llm_call_id=call, at=at)


@dataclass(frozen=True)
class World:
    owner: uuid.UUID
    stranger: uuid.UUID
    ola: uuid.UUID
    quiet: uuid.UUID
    theirs: uuid.UUID


@pytest.fixture
def world(conn: Connection) -> World:
    owner, stranger = add_user(conn), add_user(conn, "else@example.com")
    ola = add_brand(conn, owner, name="Ola", slug="ola")
    bolt = add_brand(conn, owner, name="Bolt", slug="bolt")  # sorts before Ola, listed after
    rivals = table("brand_competitors")
    conn.execute(insert(rivals).values(brand_id=ola, competitor_brand_id=bolt))
    gone = add_brand(conn, owner, name="Gone", slug="gone", archived_at=NOW)
    conn.execute(insert(rivals).values(brand_id=gone, competitor_brand_id=bolt))  # archived
    retired = add_brand(conn, owner, name="Retired", slug="retired", archived_at=NOW)
    conn.execute(insert(rivals).values(brand_id=ola, competitor_brand_id=retired))  # archived too
    quiet = add_brand(conn, owner, name="Quiet", slug="quiet")  # never scanned
    theirs = add_brand(conn, stranger, name="Other", slug="other")
    earlier = add_scan(
        conn, ola, status="succeeded", scheduled_for=NOW - HOUR, created_at=NOW - HOUR
    )
    scored(conn, earlier, {Surface.NEWS: 40}, CALM)
    latest = add_scan(conn, ola, status="partial", scheduled_for=NOW, created_at=NOW)
    scored(conn, latest, {Surface.NEWS: 60}, CALM)
    results = table("scan_surface_results")
    conn.execute(insert(results).values(scan_id=latest, surface="news", outcome="succeeded"))
    failed = {"outcome": "failed", "error_code": "serpapi.http_5xx"}
    conn.execute(insert(results).values(scan_id=latest, surface="play", **failed))
    names = ["bad", "good", "greeting", "pending"]
    ids = {n: mention(conn, ola, n) for n in names}
    for n in names:
        sighting = {"mention_id": ids[n], "scan_id": latest}
        conn.execute(insert(table("mention_observations")).values(sighting))
    label(conn, owner, ids["bad"], -1)
    label(conn, owner, ids["bad"], 1, prompt="label_mentions/v2")  # newer, not the active prompt
    label(conn, owner, ids["good"], 1)
    label(conn, owner, ids["greeting"], 1, about=False)  # not the brand: not shown
    play = {
        "source": "play_review",
        "url": None,
        "outlet": None,
        "brand_app_id": add_app(conn, ola),
    }
    review = mention(conn, ola, "review", **play)
    sighting = {"mention_id": review, "scan_id": latest}
    conn.execute(insert(table("mention_observations")).values(sighting))
    label(conn, owner, review, 1)  # the review was kind ...
    edit = {"mention_id": review, "revision": 2, "text": "Edited: awful", "scan_id": latest}
    conn.execute(insert(table("mention_revisions")).values(created_at=NOW, **edit))
    label(conn, owner, review, -1, revision=2, severity=80)  # ... until its author edited it
    later = {"status": "queued", "scheduled_for": NOW + HOUR, "created_at": NOW + HOUR}
    add_scan(conn, ola, **later)  # a newer scan, not scored yet
    alert = add(conn, table("alerts"), scan_id=latest, rule="level_increase", created_at=NOW)
    add(conn, table("notifications"), user_id=owner, alert_id=alert, title="Ola: crisis level rose",
        body="Up.", created_at=NOW)  # fmt: skip
    return World(owner, stranger, ola, quiet, theirs)


def test_an_owner_sees_their_brands_own_first(conn: Connection, world: World) -> None:
    reads = overview(conn)
    cards = reads.brands(world.owner)
    assert [(c.name, c.competitor_of) for c in cards] == [
        ("Ola", ()),
        ("Quiet", ()),
        ("Bolt", ("Ola",)),  # tracked by Ola; the archived brand's interest doesn't count
    ]
    assert (cards[0].health, cards[0].crisis, cards[0].last_status) == (60, 0, ScanStatus.QUEUED)
    assert (cards[1].health, cards[1].last_scan_at) == (None, None)
    assert [c.name for c in reads.brands(world.stranger)] == ["Other"]


def test_a_brand_page_describes_its_latest_scored_scan(conn: Connection, world: World) -> None:
    reads, owner = overview(conn), world.owner
    page = reads.brand(owner, world.ola)
    assert page is not None
    assert [p.health for p in page.trend] == [40, 60]  # oldest first
    assert [(s.surface, s.outcome, s.score) for s in page.surfaces] == [
        (Surface.NEWS, SurfaceOutcome.SUCCEEDED, 60),
        (Surface.PLAY, SurfaceOutcome.FAILED, None),
    ]
    assert [(m.text, m.sentiment) for m in page.mentions] == [
        ("Edited: awful", -1),  # the edited text and its label, the most severe first
        ("Bad", -1),
        ("Good", 1),
        ("Pending", None),
    ]
    assert page.mentions[1].reason == "Late drivers."  # the active prompt's label wins
    assert page.mentions[1].source is MentionSource.NEWS
    assert [(a.rule, a.title) for a in page.alerts] == [
        (AlertRule.LEVEL_INCREASE, "Ola: crisis level rose")
    ]
    assert [c.name for c in page.competitors] == ["Bolt"]
    empty = reads.brand(owner, world.quiet)
    assert empty is not None and (empty.trend, empty.surfaces, empty.mentions) == ((), (), ())

    assert reads.brand(world.stranger, world.ola) is None  # someone else's: as if missing
    assert reads.brand(owner, world.theirs) is None
    assert reads.brand(owner, uuid.uuid4()) is None
