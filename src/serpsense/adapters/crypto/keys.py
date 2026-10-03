"""Keys derived from SECRET_KEY with HKDF-SHA256 (ADR-0009): one per purpose, so a key used for
one thing can never verify another."""

from enum import StrEnum

from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class KeyPurpose(StrEnum):
    OTP = "otp"
    CSRF = "csrf"
    RATE_LIMIT = "rate-limit"


def derive_key(secret_key: str, purpose: KeyPurpose) -> bytes:
    hkdf = HKDF(algorithm=SHA256(), length=32, salt=None, info=f"serpsense/{purpose}".encode())
    return hkdf.derive(secret_key.encode())
