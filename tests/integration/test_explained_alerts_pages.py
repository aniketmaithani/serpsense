"""An alert's explanation on the brand page and in the notifications centre, labelled
AI-generated and escaped like any model output (ADR-0008)."""

import re
import uuid

import pytest
from sqlalchemy import Engine

from serpsense.domain.enums import CrisisComponent, Surface
from tests.integration.db_helpers import NOW, add, add_brand, add_llm_call, add_scan, table
from tests.integration.test_overview_pages import HOSTILE, signed_in
from tests.integration.test_scan_scores_view import scored

pytestmark = [pytest.mark.integration, pytest.mark.api, pytest.mark.security]

SAID = "Fares at airports drove it."


def test_an_explained_alert_says_so_on_the_brand_page_and_in_notifications(
    committing_engine: Engine,
) -> None:
    client, owner = signed_in(committing_engine)
    with committing_engine.begin() as conn:
        ola = add_brand(conn, owner, name="Ola", slug=f"ola-{uuid.uuid4().hex[:8]}")
        scan = add_scan(conn, ola, status="succeeded")
        scored(conn, scan, {Surface.NEWS: 50}, {c: 0 for c in CrisisComponent})
        explained, plain = (
            add(conn, table("alerts"), scan_id=scan, rule=rule, created_at=NOW)
            for rule in ("level_increase", "new_negative_autocomplete")
        )
        for alert, title in ((explained, "Ola: crisis level rose"), (plain, "Ola: suggestion")):
            add(conn, table("notifications"), user_id=owner, alert_id=alert, title=title,
                body="Facts.", created_at=NOW)  # fmt: skip
        call = add_llm_call(conn, owner, task="explain_crisis", prompt_version="explain_crisis/v1")
        add(conn, table("alert_explanations"), alert_id=explained, text=f"{SAID} {HOSTILE}",
            prompt_version="explain_crisis/v1", llm_call_id=call, created_at=NOW)  # fmt: skip
    for page in (client.get(f"/brands/{ola}").text, client.get("/notifications").text):
        badge = r"AI-generated(?:\)?: |</span><span>)"  # inline text, or the brand page's badge
        labelled = re.search(badge + re.escape(SAID), page)
        assert page.count(SAID) == 1 and labelled  # the explained alert only, labelled
        assert HOSTILE not in page and "&lt;script&gt;" in page
