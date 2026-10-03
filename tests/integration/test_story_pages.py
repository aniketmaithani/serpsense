"""Stories over HTTP (BUILD_PLAN §13): the narratives of a brand, each mention in its latest one,
labelled AI-generated, and the owner's only."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Connection, Engine

from tests.integration.db_helpers import (
    NOW,
    add,
    add_app,
    add_brand,
    add_llm_call,
    add_mention,
    add_user,
    table,
)
from tests.integration.test_overview_pages import HOSTILE, signed_in

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

PROMPT = {"prompt_version": "group_narratives/v1"}
GROUPING = {"task": "group_narratives", **PROMPT}


def story(conn: Connection, owner: uuid.UUID, brand: uuid.UUID, label: str) -> uuid.UUID:
    call = add_llm_call(conn, owner, **GROUPING)
    values = {"label": label, "summary": f"{label}: what riders say.", "created_at": NOW}
    return add(conn, table("narratives"), brand_id=brand, llm_call_id=call, **PROMPT, **values)


def assign(
    conn: Connection, owner: uuid.UUID, story: uuid.UUID, mention: uuid.UUID, minutes: int = 0
) -> None:
    call, at = add_llm_call(conn, owner, **GROUPING), NOW + timedelta(minutes=minutes)
    values = {"narrative_id": story, "mention_id": mention, "llm_call_id": call, "created_at": at}
    add(conn, table("narrative_assignments"), **PROMPT, **values)


def mention(conn: Connection, brand: uuid.UUID, text: str, source: str = "news") -> uuid.UUID:
    key = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    if source == "play_review":
        app = add_app(conn, brand, app_id=f"a.b{key[:6]}")
        return add_mention(conn, brand, identity_key=key, text=text, url=None, source=source,
                           outlet=None, brand_app_id=app)  # fmt: skip
    return add_mention(conn, brand, identity_key=key, text=text, url=f"https://n.in/{key[:8]}")


def test_the_owner_reads_a_brands_stories_and_each_storys_mentions(
    committing_engine: Engine,
) -> None:
    client, owner = signed_in(committing_engine)
    slug = uuid.uuid4().hex[:8]
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{slug}")
        fares = story(conn, owner, ola, "Surge fares at airports")
        hostile = story(conn, owner, ola, HOSTILE)
        moved = mention(conn, ola, "Charged triple at the airport")
        assign(conn, owner, hostile, moved)
        assign(conn, owner, fares, moved, minutes=5)  # moved: its latest story counts
        assign(conn, owner, fares, mention(conn, ola, "Airport surge again", "play_review"))
        assign(conn, owner, hostile, mention(conn, ola, HOSTILE))
        stranger = add_user(conn, f"{slug}@example.com")
        theirs = add_brand(conn, stranger, name="Theirs", slug=f"theirs-{slug}")
        their_story = story(conn, stranger, theirs, "Not yours")
        other = add_brand(conn, owner, name="Uber", slug=f"uber-{slug}")

    page = client.get(f"/brands/{ola}").text
    assert page.index("Surge fares at airports") < page.index("&lt;script&gt;")  # largest first
    assert "2 mentions on News, Play review" in page and HOSTILE not in page
    detail = client.get(f"/brands/{ola}/narratives/{fares}")
    assert detail.status_code == 200 and "AI-generated" in detail.text
    assert "Charged triple at the airport" in detail.text and "group_narratives/v1" in detail.text
    assert "Surge fares at airports: what riders say." in detail.text
    shown = client.get(f"/brands/{ola}/narratives/{hostile}").text
    assert HOSTILE not in shown and "Charged triple" not in shown  # the moved mention left it

    for brand_id, narrative_id in (
        (theirs, their_story),  # someone else's
        (ola, their_story),  # someone else's story under my brand
        (other, fares),  # my story under my other brand
        (ola, uuid.uuid4()),
        (ola, "not-an-id"),
    ):
        assert client.get(f"/brands/{brand_id}/narratives/{narrative_id}").status_code == 404
