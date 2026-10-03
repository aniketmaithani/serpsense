"""Recording a brand's stored scans for replay mode, read-only, on real Postgres."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, insert

from serpsense.adapters.db.enrichment_store import SqlEnrichmentStore
from serpsense.adapters.db.replay_export import RecordingRefused, SqlRecordingExport
from serpsense.adapters.db.search_ledger import SqlSearchLedger
from serpsense.adapters.replay.recording import Recording, text_id
from serpsense.domain.enums import (
    MentionSource,
    SerpCallOutcome,
    SerpEngine,
    ServedFrom,
    Topic,
)
from serpsense.ports.enrichment_store import MentionLabel
from serpsense.ports.search_ledger import CallRecord
from serpsense.ports.search_provider import SearchRequest
from tests.integration.db_helpers import (
    NOW,
    add_app,
    add_brand,
    add_llm_call,
    add_mention,
    add_scan,
    add_user,
    table,
)

pytestmark = pytest.mark.integration

HOUR = timedelta(hours=1)
# Requests no other test makes: replay finds its place in a recording by them (and the local
# cache is shared), so they mustn't meet another test's calls.
SUGGEST = SearchRequest(SerpEngine.GOOGLE_AUTOCOMPLETE, {"q": f"export {uuid.uuid4().hex}"})
STORY = {
    "news_results": [{"title": "Ola fares rise", "link": "https://news.in/ola"}],
    "serpapi_pagination": {"next": "https://serpapi.com/search.json?start=10"},
    "menu": [{"serpapi_link": "https://serpapi.com/search.json?engine=google_news"}],
}


def searched(
    engine: Engine, owner: uuid.UUID, scan: uuid.UUID, request: SearchRequest, **kw: Any
) -> None:
    """A successful call of the scan; its payload is kept unless it came from the local cache."""
    payload = kw.pop("payload", None)
    served = ServedFrom.LOCAL_CACHE if payload is None else ServedFrom.LIVE
    ledger = SqlSearchLedger(engine.begin)
    call = CallRecord(
        owner, scan, request, SerpCallOutcome.SUCCEEDED, served, 200, None, 5, kw.pop("at", NOW)
    )
    call_id = ledger.record_call(call)
    if payload is not None:
        ledger.store_payload(call_id, payload, at=call.created_at)


def labelled(conn: Connection, owner: uuid.UUID, mention: uuid.UUID, **kw: Any) -> None:
    prompt = kw.pop("prompt", "label_mentions/v1")
    label = MentionLabel(
        mention, kw.pop("revision", 1), -1, 60, Topic.PRICING, True, True, kw.pop("reason")
    )
    call = add_llm_call(conn, owner, prompt_version=prompt)
    at = kw.pop("at", NOW)
    SqlEnrichmentStore(conn).record([label], prompt_version=prompt, llm_call_id=call, at=at)


def test_a_brand_s_scans_become_a_recording(committing_engine: Engine) -> None:
    slug = f"ola-{uuid.uuid4().hex[:8]}"
    with committing_engine.begin() as conn:
        owner = add_user(conn, f"{slug}@example.com")
        brand = add_brand(conn, owner, name="Ola", slug=slug)
        first = add_scan(conn, brand, status="succeeded")
        second = add_scan(
            conn, brand, status="partial", scheduled_for=NOW + HOUR, created_at=NOW + HOUR
        )
        failed = {"status": "failed", "scheduled_for": NOW + 2 * HOUR, "created_at": NOW + 2 * HOUR}
        add_scan(conn, brand, **failed)
        review = {"source": "play_review", "identity_key": "gp:review-1", "url": None}
        app = add_app(conn, brand)
        story = add_mention(conn, brand, text="Fares rise", outlet=None, brand_app_id=app, **review)
        edit = {"mention_id": story, "revision": 2, "text": "Fares rise again", "scan_id": second}
        conn.execute(insert(table("mention_revisions")).values(created_at=NOW + HOUR, **edit))
        labelled(conn, owner, story, reason="Fares up.")
        labelled(conn, owner, story, revision=2, reason="Fares up again.", at=NOW + HOUR)
        later = {"at": NOW + 2 * HOUR, "prompt": "label_mentions/v2"}
        labelled(conn, owner, story, revision=2, reason="Another prompt's.", **later)
    news = SearchRequest(SerpEngine.GOOGLE_NEWS, {"q": slug})
    searched(committing_engine, owner, first, news, payload=STORY)
    searched(committing_engine, owner, second, news, at=NOW + HOUR)  # from the local cache
    searched(committing_engine, owner, second, SUGGEST, payload={"suggestions": []}, at=NOW + HOUR)

    exported = SqlRecordingExport(committing_engine).export(slug)
    assert (exported.scans, exported.answers, exported.labels) == (2, 3, 2)
    recording = Recording.model_validate_json(exported.text)
    assert recording.brand == slug and exported.text.count("\n") == 1
    first_scan, second_scan = recording.scans
    assert [(a.engine, a.payload) for a in first_scan.answers] == [(SerpEngine.GOOGLE_NEWS, 0)]
    assert {(a.engine, a.payload) for a in second_scan.answers} == {
        (SerpEngine.GOOGLE_NEWS, 0),  # the cached call replays what the request last got
        (SerpEngine.GOOGLE_AUTOCOMPLETE, 1),
    }
    assert recording.payloads[0] == {"news_results": STORY["news_results"], "menu": [{}]}
    assert {(label.text_id, label.reason) for label in recording.labels} == {
        (text_id(MentionSource.PLAY_REVIEW, "Fares rise"), "Fares up."),
        (text_id(MentionSource.PLAY_REVIEW, "Fares rise again"), "Fares up again."),  # edited
    }


def brand_with(engine: Engine, request: SearchRequest, payload: dict[str, Any]) -> str:
    """A brand of its own whose one scan made this request at NOW and kept this payload."""
    slug = f"b-{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        owner = add_user(conn, f"{slug}@example.com")
        scan = add_scan(conn, add_brand(conn, owner, slug=slug), status="succeeded")
    searched(engine, owner, scan, request, payload=payload)
    return slug


def test_each_call_keeps_its_own_payload_and_a_key_shaped_one_is_refused(
    committing_engine: Engine,
) -> None:
    exporter = SqlRecordingExport(committing_engine)
    with pytest.raises(LookupError):
        exporter.export(f"none-{uuid.uuid4().hex[:8]}")
    shaped = "0123456789abcdef" * 4
    news = SearchRequest(SerpEngine.GOOGLE_NEWS, {"q": f"export {uuid.uuid4().hex}"})
    safe = brand_with(committing_engine, news, {"news_results": []})
    leaky = brand_with(committing_engine, news, {"id": shaped})  # the same request, same time
    recording = Recording.model_validate_json(exporter.export(safe).text)
    assert recording.payloads == ({"news_results": []},)  # its own, not the other brand's
    with pytest.raises(RecordingRefused) as refused:
        exporter.export(leaky)
    assert shaped not in str(refused.value)
    assert refused.value.__cause__ is None and refused.value.__context__ is None
