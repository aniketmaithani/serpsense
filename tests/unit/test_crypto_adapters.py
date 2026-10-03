"""Key derivation and the secret box (ADR-0009, ADR-0010)."""

import pytest
from cryptography.fernet import Fernet

from serpsense.adapters.crypto.fernet_box import FernetBox
from serpsense.adapters.crypto.keys import KeyPurpose, derive_key
from serpsense.ports.secret_box import SealBroken

pytestmark = pytest.mark.unit


def test_each_purpose_gets_its_own_key() -> None:
    secret = "s" * 48
    assert derive_key(secret, KeyPurpose.OTP) == derive_key(secret, KeyPurpose.OTP)
    assert derive_key(secret, KeyPurpose.OTP) != derive_key(secret, KeyPurpose.CSRF)
    assert derive_key(secret, KeyPurpose.OTP) != derive_key("t" * 48, KeyPurpose.OTP)
    assert len(derive_key(secret, KeyPurpose.OTP)) == 32


def test_the_box_seals_with_the_newest_key_and_opens_with_any() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    sealed_before = FernetBox((old,)).seal(b"042917")
    rotated = FernetBox((new, old))
    assert rotated.open(sealed_before) == b"042917"  # old messages still open
    assert FernetBox((new,)).open(rotated.seal(b"042917")) == b"042917"  # new ones use the new key
    assert b"042917" not in rotated.seal(b"042917")
    with pytest.raises(SealBroken):
        FernetBox((new,)).open(sealed_before)  # once the old key is gone
