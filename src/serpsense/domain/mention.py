"""Mentions as parsers produce them, before they are stored (data-model §5).

A mention is identified per source: reviews and videos by the provider's id, everything else by
the sha256 hex of its canonical URL or normalised text, so a tracking parameter or a change of
case never makes a second mention. `ParsedMention` checks the same rules as the database, so a
parser can't produce a row Postgres would reject.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from serpsense.domain.enums import MentionSource

PROVIDER_ID_SOURCES = frozenset(
    {MentionSource.PLAY_REVIEW, MentionSource.MAPS_REVIEW, MentionSource.YOUTUBE_VIDEO}
)
URL_SOURCES = frozenset({MentionSource.SERP_RESULT, MentionSource.TOP_STORY, MentionSource.NEWS})
OUTLET_SOURCES = frozenset({MentionSource.NEWS, MentionSource.TOP_STORY})
REVIEW_SOURCES = frozenset({MentionSource.PLAY_REVIEW, MentionSource.MAPS_REVIEW})
MAX_TEXT, MAX_URL, MAX_OUTLET, MAX_PROVIDER_ID = 10_000, 2048, 200, 512
MAX_POSITION = 32_767  # smallint
# Parameters that only say where a click came from: on any site, and on Google's own hosts.
TRACKING = re.compile(r"utm_.*|gclid|fbclid|msclkid|igshid", re.IGNORECASE)
GOOGLE_TRACKING = re.compile(r"ved|ei|usg|sa", re.IGNORECASE)
# Always used with fullmatch: Python's `$` allows a trailing newline, Postgres' doesn't.
URL_SHAPE = re.compile(r"https?://\S+")
LANGUAGE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})*")
HEX_KEY = re.compile(r"[0-9a-f]{64}")


def canonical_url(url: str) -> str:
    """Lower-case scheme and host, tracking parameters removed, sorted query, no trailing slash.
    A fragment is kept only when it is a route (`#/…`, `#!…`), which can name another page."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    google = host.split(":")[0].removeprefix("www.").startswith("google.")

    def tracking(name: str) -> bool:
        return bool(TRACKING.fullmatch(name) or (google and GOOGLE_TRACKING.fullmatch(name)))

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = sorted((k, v) for k, v in pairs if not tracking(k))
    fragment = parts.fragment if parts.fragment.startswith(("/", "!")) else ""
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), fragment))


def normalised_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def url_key(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def text_key(text: str) -> str:
    return hashlib.sha256(normalised_text(text).encode()).hexdigest()


def clean_url(url: object) -> str | None:
    """A URL the database accepts, or None."""
    if isinstance(url, str) and URL_SHAPE.fullmatch(url) and len(url) <= MAX_URL:
        return url
    return None


def clean_language(code: object) -> str | None:
    """A lowercase BCP-47 tag, or None."""
    if isinstance(code, str) and LANGUAGE.fullmatch(code.lower()):
        return code.lower()
    return None


@dataclass(frozen=True)
class ParsedMention:
    """One thing a surface showed about the brand, and where it ranked there."""

    source: MentionSource
    identity_key: str
    text: str
    url: str | None = None
    outlet: str | None = None
    language_code: str | None = None
    published_at: datetime | None = None
    position: int | None = None  # rank on the surface, from 1
    star_rating: int | None = None  # 1-5, reviews only

    def __post_init__(self) -> None:
        _check_identity(self.source, self.identity_key)
        _check_content(self)
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        if self.position is not None and (
            isinstance(self.position, bool) or not 1 <= self.position <= MAX_POSITION
        ):
            raise ValueError("position is a rank from 1 that fits a smallint")
        if self.star_rating is not None and (
            self.source not in REVIEW_SOURCES or not 1 <= self.star_rating <= 5
        ):
            raise ValueError("a star rating is 1-5, and only on reviews")


def _check_identity(source: MentionSource, key: str) -> None:
    if source in PROVIDER_ID_SOURCES:
        if not 1 <= len(key) <= MAX_PROVIDER_ID or "\x00" in key:
            raise ValueError("a provider id is 1-512 characters")
    elif not HEX_KEY.fullmatch(key):
        raise ValueError("identity key: sha256 hex, or the provider's id for reviews and videos")


def _check_content(mention: ParsedMention) -> None:
    texts = [mention.text, mention.url or "", mention.outlet or ""]
    if any("\x00" in text for text in texts):
        raise ValueError("Postgres text can't hold NUL characters")
    for text in texts:
        text.encode()  # a lone surrogate raises UnicodeEncodeError, a ValueError
    if not mention.text.strip() or len(mention.text) > MAX_TEXT:
        raise ValueError("text must be non-blank and at most 10 000 characters")
    if mention.url is not None and (
        clean_url(mention.url) is None or mention.source in REVIEW_SOURCES
    ):
        raise ValueError("url must be http(s), at most 2048 characters, and never on a review")
    if mention.outlet is not None and (
        mention.source not in OUTLET_SOURCES or len(mention.outlet) > MAX_OUTLET
    ):
        raise ValueError("outlet is a news publisher of at most 200 characters")
    if (
        mention.language_code is not None
        and clean_language(mention.language_code) != mention.language_code
    ):
        raise ValueError("language code must be lowercase BCP-47")
