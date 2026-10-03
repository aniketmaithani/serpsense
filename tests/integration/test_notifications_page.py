"""The notifications centre over HTTP (BUILD_PLAN §13): the user's own, with read marks."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import Engine, select

from tests.integration.db_helpers import NOW, add, add_brand, add_scan, add_user, table
from tests.integration.test_login_pages import token
from tests.integration.test_overview_pages import HOSTILE, signed_in

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

READS = table("notification_reads")


def notify(engine: Engine, user: uuid.UUID, title: str, **values: object) -> uuid.UUID:
    with engine.begin() as conn:
        row = {"user_id": user, "title": title, "body": "Details.", "created_at": NOW, **values}
        return add(conn, table("notifications"), **row)


def test_the_user_reads_and_marks_their_own_notifications(committing_engine: Engine) -> None:
    client, user = signed_in(committing_engine)
    slug = uuid.uuid4().hex[:8]
    with committing_engine.begin() as conn:
        ola = add_brand(conn, user, name="Ola", slug=f"ola-{slug}")
        scan = add_scan(conn, ola, status="succeeded")
        alert = add(conn, table("alerts"), scan_id=scan, rule="level_increase", created_at=NOW)
        stranger = add_user(conn, f"{slug}@example.com")
    about = notify(committing_engine, user, "Ola: crisis level rose", alert_id=alert)
    later = NOW + timedelta(minutes=5)
    hostile = notify(committing_engine, user, HOSTILE, body=HOSTILE, created_at=later)
    theirs = notify(committing_engine, stranger, "Not yours")

    page = client.get("/notifications")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    text = page.text
    assert "Ola: crisis level rose" in text and f'href="/brands/{ola}"' in text
    assert "Not yours" not in text and HOSTILE not in text and "&lt;script&gt;" in text
    assert text.index("&lt;script&gt;") < text.index("Ola: crisis")  # newest first
    assert '<span class="count">2</span>' in text and "Mark all read" in text
    form = token(text)

    assert client.post("/notifications/read", data={"csrf_token": "forged"}).status_code == 403
    one = {"csrf_token": form, "notification_id": str(about)}
    assert client.post("/notifications/read", data=one).headers["location"] == "/notifications"
    assert '<span class="count">1</span>' in client.get("/").text  # the layout counts too
    for other in (str(theirs), "not-an-id", ""):
        client.post("/notifications/read", data={"csrf_token": form, "notification_id": other})
    arrived = notify(committing_engine, user, "Arrived since", created_at=later + timedelta(1))
    shown = {"csrf_token": form, "notification_id": str(hostile), "and_older": "true"}
    client.post("/notifications/read", data=shown)  # "mark all", as the page sent it
    with committing_engine.connect() as conn:
        read = set(conn.execute(select(READS.c.notification_id)).scalars())
    assert {about, hostile} <= read and theirs not in read
    assert arrived not in read  # newer than the page the user saw
    client.post("/notifications/read", data={"csrf_token": form, "notification_id": str(arrived)})
    after = client.get("/notifications").text
    assert 'class="count"' not in after and "Mark all read" not in after


def test_signed_out_the_centre_sends_you_to_sign_in(committing_engine: Engine) -> None:
    client, _ = signed_in(committing_engine)
    client.cookies.clear()
    assert client.get("/notifications").headers["location"] == "/login"
    assert client.post("/notifications/read", data={}).headers["location"] == "/login"
