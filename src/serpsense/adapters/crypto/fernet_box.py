"""SecretBox on MultiFernet (ADR-0010): the first key seals, every key opens, so keys rotate by
putting the new one first."""

from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from serpsense.ports.secret_box import SealBroken


class FernetBox:
    def __init__(self, keys: Sequence[str]) -> None:
        """`keys` newest first, as settings parse OUTBOX_ENCRYPTION_KEYS."""
        self._fernet = MultiFernet([Fernet(key) for key in keys])

    def seal(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def open(self, sealed: bytes) -> bytes:
        try:
            return self._fernet.decrypt(sealed)
        except InvalidToken as exc:
            raise SealBroken("the sealed value can't be opened") from exc
