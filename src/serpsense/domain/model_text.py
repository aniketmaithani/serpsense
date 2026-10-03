"""What model text may carry before a person reads it, or an email sends it (ADR-0008).

Explanations go straight into the owner's email, and drafts are copied into public replies, so
text the model writes must not carry links, phone numbers or email addresses (a mention could
have planted them), nor invisible format characters that hide what it says.
"""

import re
import unicodedata

CONTACT = re.compile(
    r"https?://|www\.|\b[\w.+-]+@[\w-]+\.[\w.]+\b|\+?\d[\d\s-]{7,}\d", re.IGNORECASE
)


def has_contact(text: str) -> bool:
    """A link, an email address or a phone number."""
    return CONTACT.search(text) is not None


def has_invisible(text: str) -> bool:
    """A format character (bidi controls, zero-width marks, tag characters)."""
    return any(unicodedata.category(char) == "Cf" for char in text)
