"""Redaction of SerpApi payloads before anything stores or returns them (ADR-0007, AGENTS §6).

At any depth it removes:
- the key: any `api_key` field, and any key or string containing `api_key=` or the key itself;
- every URL in `search_metadata` (endpoints and archived HTML of the search);
- author identity, as the recorded responses show it: news authors, YouTube channels, Play
  and Maps reviewers (`title`/`avatar`, `user`, `username`, `contributor_id`), the developer's
  reply to a review (it greets the reviewer by name), short-video creators (`short_videos`, whose
  links lead to personal profiles, and any `profile_name`) and the Play developer's contact
  details.

Removing a field (not masking it) keeps the result valid for the no-key database CHECK.
"""

from collections.abc import Mapping
from typing import Any

from serpsense.domain.search import contains_api_key

# Fields that name or picture a person, wherever they appear.
IDENTITY_FIELDS = frozenset(
    {
        "api_key",
        "author",
        "authors",
        "channel",
        "channel_results",
        "contributor_id",
        "developer_contact",
        "profile_name",
        "short_videos",
        "user",
        "username",
    }
)
# Lists of reviews: Play/Maps `reviews` and the Maps place page's `most_relevant`.
REVIEW_LISTS = frozenset({"reviews", "most_relevant"})
# Inside a review, `title` is the reviewer's name (Play), `avatar`/`link` lead to them, and the
# developer's `response` addresses them by name.
REVIEWER_FIELDS = frozenset({"title", "avatar", "link", "response"})


def redact(payload: Mapping[str, Any], *, api_key: str) -> dict[str, Any]:
    """A redacted copy of a SerpApi JSON object; the input is left untouched."""
    if not api_key:
        raise ValueError("redaction needs the configured key to look for")
    redacted = _object(payload, api_key=api_key, drop=frozenset())
    if "search_metadata" in redacted:
        metadata = redacted.pop("search_metadata")
        if isinstance(metadata, dict):  # SerpApi always sends an object; anything else goes
            redacted["search_metadata"] = _without_urls(metadata)
    return redacted


def _without_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_urls(item) for key, item in value.items() if not _is_url(item)}
    if isinstance(value, list):
        return [_without_urls(item) for item in value if not _is_url(item)]
    return value


def _is_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _leaks(text: str, api_key: str) -> bool:
    return api_key in text or contains_api_key(text)


def _object(value: Mapping[str, Any], *, api_key: str, drop: frozenset[str]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, item in value.items():
        if key in IDENTITY_FIELDS or key in drop or _leaks(key, api_key):
            continue
        if isinstance(item, str) and _leaks(item, api_key):
            continue
        reviews = key in REVIEW_LISTS and isinstance(item, list | tuple)
        kept[key] = _value(item, api_key=api_key, drop=REVIEWER_FIELDS if reviews else frozenset())
    return kept


def _value(value: Any, *, api_key: str, drop: frozenset[str]) -> Any:
    if isinstance(value, Mapping):
        return _object(value, api_key=api_key, drop=drop)
    if isinstance(value, list | tuple):
        return [
            _value(item, api_key=api_key, drop=drop)
            for item in value
            if not (isinstance(item, str) and _leaks(item, api_key))
        ]
    return value
