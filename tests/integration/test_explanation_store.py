"""What the explainer is told about an alert, read from Postgres, and its explanation written
once (data-model §6, §8)."""

import hashlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, insert, update

from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.adapters.db.explanation_store import SqlExplanationStore
from serpsense.domain.enums import AlertRule, CrisisComponent, MentionSource, Surface, Topic
from serpsense.ports.enrichment_store import MentionLabel
from tests.integration.db_helpers import (
    NOW,
    add,
    add_brand,
    add_llm_call,
    add_mention,
    add_scan,
    add_user,
    table,
)
from tests.integration.test_scan_scores_view import scored

pytestmark = pytest.mark.integration

GROUP = {"task": "group_narratives", "prompt_version": "group_narratives/v1"}
EXPLAIN = {"task": "explain_crisis", "prompt_version": "explain_crisis/v1"}


class Brand:
    def __init__(self, conn: Connection) -> None:
        self.conn, self.owner = conn, add_user(conn)
        self.brand = add_brand(conn, self.owner, name="Ola", slug="ola")
        earlier = add_scan(conn, self.brand, status="succeeded", created_at=NOW - timedelta(1))
        scored(conn, earlier, {Surface.NEWS: 70}, {CrisisComponent.VELOCITY: 10})
        self.scan = add_scan(conn, self.brand, status="succeeded", scheduled_for=NOW + timedelta(1))
        scored(conn, self.scan, {Surface.NEWS: 40}, {CrisisComponent.VELOCITY: 70})

    def mention(self, text: str, sentiment: int, severity: int = 40, **kw: Any) -> uuid.UUID:
        key = hashlib.sha256(text.encode()).hexdigest()
        values = {"identity_key": key, "text": text, "url": f"https://n.in/{key[:8]}", **kw}
        mention = add_mention(self.conn, self.brand, **values)
        self.conn.execute(
            insert(table("mention_observations")).values(mention_id=mention, scan_id=self.scan)
        )
        label = MentionLabel(mention, 1, sentiment, severity, Topic.PRICING, sentiment < 0, True,
                             "Because.")  # fmt: skip
        call = add_llm_call(self.conn, self.owner)
        SqlEnrichmentStore(self.conn).record(
            [label], prompt_version="label_mentions/v1", llm_call_id=call, at=NOW
        )
        return mention

    def alert(self, rule: AlertRule, narrative: uuid.UUID | None = None) -> uuid.UUID:
        values = {"scan_id": self.scan, "rule": rule, "narrative_id": narrative}
        return add(self.conn, table("alerts"), created_at=NOW, **values)

    def story(self, *mentions: uuid.UUID) -> uuid.UUID:
        call = add_llm_call(self.conn, self.owner, **GROUP)
        values = {"label": "Airport surge", "summary": "Fares triple.", "created_at": NOW}
        story = add(self.conn, table("narratives"), brand_id=self.brand, llm_call_id=call,
                    prompt_version="group_narratives/v1", **values)  # fmt: skip
        for mention in mentions:
            self.assign(story, mention)
        return story

    def assign(self, story: uuid.UUID, mention: uuid.UUID, minutes: int = 0) -> None:
        call = add_llm_call(self.conn, self.owner, **GROUP)
        at = NOW + timedelta(minutes=minutes)
        add(self.conn, table("narrative_assignments"), narrative_id=story, mention_id=mention,
            llm_call_id=call, prompt_version="group_narratives/v1", created_at=at)  # fmt: skip


def test_a_level_alert_is_told_with_the_scans_scores_and_worst_mentions(conn: Connection) -> None:
    ola = Brand(conn)
    ola.mention("Charged triple at the airport", -1, severity=60)
    ola.mention("Driver was late", -1, severity=30)
    ola.mention("Great ride", 1)
    alert = ola.alert(AlertRule.LEVEL_INCREASE)
    brief = SqlExplanationStore(conn).brief(alert)
    assert brief is not None and (brief.owner_id, brief.brand_name, brief.scan_id) == (
        ola.owner, "Ola", ola.scan,
    )  # fmt: skip
    assert (brief.rule, brief.health, brief.competitor, brief.explained) == (
        AlertRule.LEVEL_INCREASE, 40, False, False,
    )  # fmt: skip
    assert brief.components == {CrisisComponent.VELOCITY: 70}
    assert [m.text for m in brief.mentions] == ["Charged triple at the airport", "Driver was late"]
    assert SqlExplanationStore(conn).brief(uuid.uuid4()) is None


def test_a_suggestion_alert_is_told_with_the_negative_suggestions_only(conn: Connection) -> None:
    ola = Brand(conn)
    ola.mention("Charged triple at the airport", -1)
    suggestion = {"source": "autocomplete", "url": None, "outlet": None, "published_at": None}
    ola.mention("ola scam", -1, **suggestion)
    brief = SqlExplanationStore(conn).brief(ola.alert(AlertRule.NEW_NEGATIVE_AUTOCOMPLETE))
    assert brief is not None
    assert [(m.source, m.text) for m in brief.mentions] == [
        (MentionSource.AUTOCOMPLETE, "ola scam")
    ]


def test_a_spreading_story_is_told_with_its_current_mentions(conn: Connection) -> None:
    ola = Brand(conn)
    fares = ola.mention("Charged triple at the airport", -1)
    moved = ola.mention("Driver was late", -1)
    story = ola.story(fares, moved)
    ola.assign(ola.story(), moved, minutes=5)  # moved to another story since
    brief = SqlExplanationStore(conn).brief(ola.alert(AlertRule.NARRATIVE_SPREAD, story))
    assert brief is not None and (brief.story_label, brief.story_summary) == (
        "Airport surge", "Fares triple.",
    )  # fmt: skip
    assert [m.text for m in brief.mentions] == ["Charged triple at the airport"]


def test_a_brief_says_when_the_brand_is_gone(conn: Connection) -> None:
    ola = Brand(conn)
    alert, store = ola.alert(AlertRule.LEVEL_INCREASE), SqlExplanationStore(conn)
    brief = store.brief(alert)
    assert brief is not None and not brief.gone
    conn.execute(update(table("brands")).values(archived_at=NOW))
    archived = store.brief(alert)
    assert archived is not None and archived.gone


def test_an_alert_is_explained_once(conn: Connection) -> None:
    ola = Brand(conn)
    alert, store = ola.alert(AlertRule.LEVEL_INCREASE), SqlExplanationStore(conn)
    call = add_llm_call(conn, ola.owner, **EXPLAIN)
    said = {"text": "It rose.", "prompt_version": "explain_crisis/v1", "llm_call_id": call}
    assert store.record(alert, at=NOW, **said)
    assert not store.record(alert, at=NOW, **{**said, "text": "Again."})
    brief = store.brief(alert)
    assert brief is not None and brief.explained
