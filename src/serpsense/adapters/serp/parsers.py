"""Parsers: redacted SerpApi payloads → ParsedMention (data-model §5).

Payloads are external data, so every field is checked and an item that can't make a valid
mention is skipped rather than failing the surface. A mention that appears twice on one page
keeps its best rank; the collector keeps the best rank across a scan's calls.

Only shapes seen in recorded responses are parsed (ADR-0007): top stories and news story
clusters follow once a response showing them is recorded.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, TypeGuard

from serpsense.domain.enums import MentionSource
from serpsense.domain.mention import (
    MAX_OUTLET,
    MAX_TEXT,
    ParsedMention,
    best_ranked,
    clean_language,
    clean_url,
    text_key,
    url_key,
)
from serpsense.domain.observation import AppRating, TrendsPoint
from serpsense.observability import get_logger

log = get_logger(__name__)

RELATED_LIST = 25  # Google Trends shows at most 25 rising and 25 top related queries


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


def parse_trends_related(payload: Mapping[str, Any]) -> list[ParsedMention]:
    """Related queries from `google_trends` RELATED_QUERIES. Rising ones (the early warning) rank
    1-25 and top ones 26-50, so a rank says which list a query came from whatever its length."""
    related = payload.get("related_queries")
    related = related if isinstance(related, Mapping) else {}
    rising = _ranked(related, "rising")[:RELATED_LIST]
    top = [(RELATED_LIST + rank, item) for rank, item in _ranked(related, "top")]
    language = _language(payload)
    return _best_ranks(
        (
            _safely(_text_mention, MentionSource.TRENDS_QUERY, item.get("query"), language, rank)
            for rank, item in [*rising, *top]
        ),
        "trends",
    )


def parse_trends_timeseries(payload: Mapping[str, Any]) -> list[TrendsPoint]:
    """Interest over time from `google_trends` TIMESERIES, one point per query per time."""
    over_time = payload.get("interest_over_time")
    over_time = over_time if isinstance(over_time, Mapping) else {}
    points: list[TrendsPoint] = []
    for moment in _items(over_time, "timeline_data"):
        at = _unix_time(moment.get("timestamp"))
        partial = moment.get("partial_data") is True
        for value in _items(moment, "values"):
            point = _safely_point(value, at, partial)
            if point is not None:
                points.append(point)
    return points


def parse_play_product(payload: Mapping[str, Any]) -> tuple[AppRating | None, list[ParsedMention]]:
    """The app's rating and its reviews from a `google_play_product` page."""
    language = _language(payload)
    reviews = _best_ranks(
        (_safely(_review, item, language, rank) for rank, item in _ranked(payload, "reviews")),
        "play",
    )
    return _app_rating(payload.get("product_info")), reviews


def _safely_point(
    value: Mapping[str, Any], at: datetime | None, partial: bool
) -> TrendsPoint | None:
    query, index = _string(value.get("query")), value.get("query_index")
    interest = value.get("extracted_value")
    if at is None or query is None or not _is_int(index) or not _is_int(interest):
        return None
    try:
        return TrendsPoint(query, index, at, interest, partial)
    except ValueError:
        return None


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _review(item: Mapping[str, Any], language: str | None, rank: int) -> ParsedMention | None:
    review_id, text = _string(item.get("id")), _string(item.get("snippet"))
    if review_id is None or text is None:
        return None
    stars = item.get("rating")
    return ParsedMention(
        source=MentionSource.PLAY_REVIEW,
        identity_key=review_id,
        text=_text(text),
        language_code=language,
        published_at=_timestamp(item.get("iso_date")),
        position=rank,
        star_rating=int(stars) if isinstance(stars, int | float) and 1 <= stars <= 5 else None,
    )


def _app_rating(info: object) -> AppRating | None:
    """None while the store shows no rating yet (data-model §5: no row then)."""
    if not isinstance(info, Mapping):
        return None
    rating, count = info.get("rating"), info.get("reviews")
    if not isinstance(rating, int | float) or not isinstance(count, int) or isinstance(count, bool):
        return None
    try:
        hundredths = int((Decimal(str(rating)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return AppRating(rating_hundredths=hundredths, review_count=count)
    except (ValueError, InvalidOperation):
        return None


def _unix_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


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


def _best_ranks(found: Iterable[ParsedMention | None], surface: str) -> list[ParsedMention]:
    """One mention per identity, at its best rank. Skipped items are logged per surface, so a
    change in SerpApi's format shows up instead of quietly giving no mentions."""
    items = list(found)
    mentions = [mention for mention in items if mention is not None]
    if skipped := len(items) - len(mentions):
        log.info("serp_parse.items_skipped", surface=surface, count=skipped)
    return best_ranked(mentions)
