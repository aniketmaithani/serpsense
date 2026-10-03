"""Parsers: redacted SerpApi payloads → ParsedMention (data-model §5).

Payloads are external data, so every field is checked and an item that can't make a valid
mention is skipped rather than failing the surface. A mention that appears twice on one page
keeps its best rank; the collector keeps the best rank across a scan's calls.

Only shapes seen in recorded responses are parsed (ADR-0007): top stories and news story
clusters follow once a response showing them is recorded.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import (
    MAX_OUTLET,
    MAX_TEXT,
    ParsedMention,
    clean_language,
    clean_url,
    text_key,
    url_key,
)
from serpsense.observability import get_logger

log = get_logger(__name__)


def parse_search_page(payload: Mapping[str, Any]) -> list[ParsedMention]:
    """Organic results and People also ask from a `google` search page."""
    language = _language(payload)
    found = [_safely(_organic, item, language) for item in _items(payload, "organic_results")]
    found += [
        _safely(_question, item, language, rank)
        for rank, item in _ranked(payload, "related_questions")
    ]
    return _best_ranks(found, "search_page")


def parse_autocomplete(payload: Mapping[str, Any]) -> list[ParsedMention]:
    """Suggestions from `google_autocomplete`, ranked in the order Google shows them."""
    language = _language(payload)
    return _best_ranks(
        (
            _safely(_text_mention, MentionSource.AUTOCOMPLETE, item.get("value"), language, rank)
            for rank, item in _ranked(payload, "suggestions")
        ),
        "autocomplete",
    )


def parse_news(payload: Mapping[str, Any]) -> list[ParsedMention]:
    """Articles from `google_news`, with their publisher and time."""
    language = _language(payload)
    return _best_ranks(
        (
            _safely(_article, item, language, rank)
            for rank, item in _ranked(payload, "news_results")
        ),
        "news",
    )


def _safely(build: Callable[..., ParsedMention | None], *args: Any) -> ParsedMention | None:
    """An item that can't make a valid mention (a lone surrogate, a NUL, …) is skipped."""
    try:
        return build(*args)
    except (ValueError, OverflowError):  # includes UnicodeEncodeError
        return None


def _organic(item: Mapping[str, Any], language: str | None) -> ParsedMention | None:
    url, title = clean_url(item.get("link")), _string(item.get("title"))
    if url is None or title is None:
        return None
    rank = item.get("position")
    rank = rank if isinstance(rank, int) and not isinstance(rank, bool) else None
    return ParsedMention(
        source=MentionSource.SERP_RESULT,
        identity_key=url_key(url),
        text=_text(title, _string(item.get("snippet"))),
        url=url,
        language_code=language,
        position=rank if rank is not None and 1 <= rank <= 32_767 else None,
    )


def _article(item: Mapping[str, Any], language: str | None, rank: int) -> ParsedMention | None:
    url, title = clean_url(item.get("link")), _string(item.get("title"))
    if url is None or title is None:
        return None
    outlet = item.get("source")
    outlet = outlet.get("name") if isinstance(outlet, Mapping) else outlet
    return ParsedMention(
        source=MentionSource.NEWS,
        identity_key=url_key(url),
        text=_text(title),
        url=url,
        outlet=(_string(outlet) or "")[:MAX_OUTLET] or None,
        language_code=language,
        published_at=_timestamp(item.get("iso_date")),
        position=rank,
    )


def _question(item: Mapping[str, Any], language: str | None, rank: int) -> ParsedMention | None:
    return _text_mention(MentionSource.PEOPLE_ALSO_ASK, item.get("question"), language, rank)


def _text_mention(
    source: MentionSource, value: object, language: str | None, rank: int
) -> ParsedMention | None:
    text = _string(value)
    if text is None:
        return None
    text = _text(text)
    return ParsedMention(
        source=source,
        identity_key=text_key(text),
        text=text,
        language_code=language,
        position=rank,
    )


def _items(container: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = container.get(key)
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _ranked(container: Mapping[str, Any], key: str) -> list[tuple[int, Mapping[str, Any]]]:
    return list(enumerate(_items(container, key), start=1))


def _language(payload: Mapping[str, Any]) -> str | None:
    parameters = payload.get("search_parameters")
    return clean_language(parameters.get("hl")) if isinstance(parameters, Mapping) else None


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _text(*parts: str | None) -> str:
    return "\n".join(part for part in parts if part)[:MAX_TEXT]


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
        return parsed.astimezone(UTC) if parsed is not None and parsed.tzinfo is not None else None
    except (ValueError, OverflowError):  # unparseable, or out of range once in UTC
        return None


def _rank(mention: ParsedMention) -> int:
    return mention.position or 10**6  # unranked mentions sort last


def _best_ranks(found: Iterable[ParsedMention | None], surface: str) -> list[ParsedMention]:
    """One mention per identity, at its best rank. Skipped items are logged per surface, so a
    change in SerpApi's format shows up instead of quietly giving no mentions."""
    best: dict[tuple[MentionSource, str], ParsedMention] = {}
    skipped = 0
    for mention in found:
        if mention is None:
            skipped += 1
            continue
        key = (mention.source, mention.identity_key)
        if key not in best or _rank(mention) < _rank(best[key]):
            best[key] = mention
    if skipped:
        log.info("serp_parse.items_skipped", surface=surface, count=skipped)
    return sorted(best.values(), key=lambda m: (m.source, _rank(m)))
