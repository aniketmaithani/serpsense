"""The code page in the landing page's look (issue #183), on real Postgres: six digits a phone
can fill in from the email, and a wrong code marks the field."""

import pytest
from sqlalchemy import Engine

from tests.integration.test_login_pages import browser, token

pytestmark = [pytest.mark.integration, pytest.mark.api]

EMAIL = "code-look@example.com"
SHEET = '<link rel="stylesheet" href="/static/landing/signin.css">'
CODE = 'class="code" name="code" inputmode="numeric" autocomplete="one-time-code"'
INVALID = 'aria-invalid="true" aria-describedby="problem"'


def test_the_code_page_asks_for_six_digits_and_marks_a_wrong_one(committing_engine: Engine) -> None:
    client = browser(committing_engine)
    form = token(client.get("/login").text)
    sent = client.post("/login", data={"form_token": form, "email": EMAIL})
    assert sent.status_code == 200 and SHEET in sent.text and INVALID not in sent.text
    assert CODE in sent.text and 'pattern="[0-9]{6}" maxlength="6"' in sent.text
    assert f'<input type="hidden" name="email" value="{EMAIL}">' in sent.text
    assert "<script" not in sent.text and "Not affiliated with SerpApi, LLC." in sent.text
    wrong = client.post("/verify", data={"form_token": form, "email": EMAIL, "code": "000000"})
    assert wrong.status_code == 400 and INVALID in wrong.text
