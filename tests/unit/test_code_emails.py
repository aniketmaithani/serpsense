"""What a code's email says, by what the code is for (ADR-0009, ADR-0013)."""

import uuid
from dataclasses import replace

import pytest

from serpsense.adapters.crypto.fernet_box import FernetBox
from serpsense.domain.enums import CodeEmail, OutboxKind
from serpsense.ports.outbox import DueEmail
from serpsense.services.outbox import render
from tests.factories import TEST_FERNET_KEY

pytestmark = pytest.mark.unit

BOX = FernetBox((TEST_FERNET_KEY,))


def test_a_deletion_code_says_it_deletes_the_account() -> None:
    due = DueEmail(
        uuid.uuid4(),
        OutboxKind.OTP_EMAIL,
        "someone@example.com",
        CodeEmail.DELETE_ACCOUNT,
        {"minutes": "10"},
        BOX.seal(b"123456"),
    )
    deleting = render(due, BOX)
    assert deleting.subject == "Your code to delete your SerpSense account"
    assert "123456" in deleting.text and "This code deletes your SerpSense account" in deleting.text
    assert "sign out everywhere" in deleting.text
    signing_in = render(replace(due, template=CodeEmail.SIGN_IN), BOX)
    assert signing_in.subject == "Your SerpSense sign-in code" and "delete" not in signing_in.text
