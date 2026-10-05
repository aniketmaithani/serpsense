"""The demo story as replay recordings (adapters/replay/recording.py), built in code.

Each brand's scans ask what the real collectors ask for its settings, and each request is
answered from the story's content as it stood at that scan: what was on the page, the newest
articles and reviews, the suggestions in their order, Trends up to that day. Every text is
labelled, and the complaints are put in their stories, at the prompt versions the app uses, so
replay mode plays the story through the whole pipeline (collect, label, group, score, alert)
with no key and no cost. Built from fixed dates, so a second load plays nothing new.
"""

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from serpsense.adapters.replay.recording import (
    RecordedAnswer,
    RecordedDraft,
    RecordedExplanation,
    RecordedLabel,
    RecordedNarrative,
    RecordedScan,
    Recording,
    review_id,
    text_id,
)
from serpsense.adapters.replay.story import words
from serpsense.adapters.replay.story.content import (
    STORIES,
    Article,
    Content,
    Query,
    Result,
    Review,
    StoryBrand,
    Tag,
)
from serpsense.adapters.replay.story.soundnest import RIVAL
from serpsense.adapters.replay.story.voltbox import MAIN
from serpsense.adapters.serp import parsers
from serpsense.adapters.serp.collectors import COLLECTORS
from serpsense.domain.enums import LlmTask, SerpEngine
from serpsense.domain.labelling import PROMPTS
from serpsense.domain.mention import ParsedMention
from serpsense.domain.settings.search import resolve
from serpsense.ports.collector import App, Subject, Target
from serpsense.ports.search_provider import SearchRequest

START = datetime(2026, 9, 21, 3, 30, tzinfo=UTC)  # 09:00 in India, the story's first morning
STEP = timedelta(hours=12)
SCANS = 21  # VoltBox's scans, twice a day, ending on the morning the crisis peaks
# The prompts the app asks with (services/grouping, explanations, drafts; a test keeps them so).
LABEL_PROMPT = PROMPTS[LlmTask.LABEL_MENTIONS]
GROUP_PROMPT, EXPLAIN_PROMPT, DRAFT_PROMPT = (
    "group_narratives/v1",
    "explain_crisis/v1",
    "draft_response/v1",
)
LANGUAGE = {"search_parameters": {"hl": "en"}}
SHOWN = 10  # results, articles and reviews a page shows
Params = Mapping[str, str | int]
Answer = dict[str, Any]


BRANDS = (MAIN, RIVAL)  # the main brand, then its competitors


def recordings() -> tuple[Recording, ...]:
    return tuple(_recording(brand) for brand in BRANDS)


def _recording(brand: StoryBrand) -> Recording:
    aim = target(brand)
    snapshot = aim.settings.model_dump(mode="json")
    kept: dict[str, int] = {}
    payloads: list[dict[str, Any]] = []
    scans: list[RecordedScan] = []
    for step in range(0, SCANS, brand.every):
        answers = []
        for collector in COLLECTORS:
            for lead in collector.leads(aim):
                payload = _payload(brand, lead.request, step)
                index = kept.setdefault(json.dumps(payload, sort_keys=True), len(payloads))
                if index == len(payloads):
                    payloads.append(payload)
                params = {name: str(value) for name, value in lead.request.params.items()}
                answers.append(
                    RecordedAnswer(engine=lead.request.engine, params=params, payload=index)
                )
        at = START + step * STEP + brand.offset
        scans.append(RecordedScan(recorded_at=at, settings=snapshot, answers=tuple(answers)))
    tagged = _tagged(brand)
    return Recording(
        format=1,
        brand=brand.slug,
        name=brand.name,
        payloads=tuple(payloads),
        scans=tuple(scans),
        labels=tuple(_label(mention, tag) for mention, tag in tagged),
        narratives=tuple(_story(mention, tag) for mention, tag in tagged if tag.story),
        explanations=_explanations() if brand is MAIN else (),
        drafts=_drafts(tagged) if brand is MAIN else (),
    )


def target(brand: StoryBrand) -> Target:
    """The brand as its scans aim it; the ids are made up, as no request uses them."""
    rivals = [RIVAL] if brand is MAIN else []

    def subject(of: StoryBrand) -> Subject:
        return Subject(uuid.uuid5(uuid.NAMESPACE_DNS, f"{of.slug}.example.com"), of.name)

    return Target(
        subject(brand),
        resolve(brand.settings),
        competitors=tuple(subject(rival) for rival in rivals),
        apps=(App(uuid.uuid5(uuid.NAMESPACE_DNS, brand.app), brand.app),),
    )


def _payload(brand: StoryBrand, request: SearchRequest, step: int) -> dict[str, Any]:
    """SerpApi's answer to the request at this step, holding only what the parsers read."""
    answer = ANSWERS.get(request.engine)
    if answer is None:  # a collector the story doesn't know
        raise ValueError(f"the story has nothing for {request.engine}")
    return LANGUAGE | answer(brand.content, request.params, step, brand)


def _suggestions(content: Content, params: Params, step: int, brand: StoryBrand) -> Answer:
    shown = sorted((s for s in content.suggestions if s.at <= step), key=lambda s: s.rank)
    return {"suggestions": [{"value": s.value} for s in shown]}


def _news(content: Content, params: Params, step: int, brand: StoryBrand) -> Answer:
    return {"news_results": [_article(a) for a in _newest(content.articles, step)]}


def _trends(content: Content, params: Params, step: int, brand: StoryBrand) -> Answer:
    if params.get("data_type") == "TIMESERIES":
        return {"interest_over_time": {"timeline_data": _interest(step)}}
    return {"related_queries": _related(content, step)}


def _play(content: Content, params: Params, step: int, brand: StoryBrand) -> Answer:
    if params.get("all_reviews"):
        return {"reviews": [_review(brand, r) for r in _newest(content.reviews, step)]}
    stars = max((s for at, s in content.rating if at <= step), default=None)
    if stars is None:
        raise ValueError(f"the story gives {brand.slug} no rating by scan {step}")
    return {"product_info": {"rating": stars, "reviews": 1200 + 37 * step}}


def _search_page(content: Content, params: Params, step: int, brand: StoryBrand) -> Answer:
    results = sorted((r for r in content.results if r.at <= step), key=lambda r: r.rank)
    questions = sorted(
        (q for q in content.questions if q.at <= step), key=lambda q: (q.rank, -q.at)
    )
    return {
        "organic_results": [
            {"link": r.link, "title": r.title, "snippet": r.snippet, "position": n}
            for n, r in enumerate(results[:SHOWN], start=1)
        ],
        "related_questions": [{"question": q.text} for q in questions[:4]],
    }


ANSWERS: Mapping[SerpEngine, Callable[[Content, Params, int, StoryBrand], Answer]] = {
    SerpEngine.GOOGLE: _search_page,
    SerpEngine.GOOGLE_AUTOCOMPLETE: _suggestions,
    SerpEngine.GOOGLE_NEWS: _news,
    SerpEngine.GOOGLE_TRENDS: _trends,
    SerpEngine.GOOGLE_PLAY_PRODUCT: _play,
}


def _newest(items: Sequence[Any], step: int) -> list[Any]:
    return sorted((i for i in items if i.at <= step), key=lambda i: -i.at)[:SHOWN]


def _when(step: int, before: timedelta) -> str:
    return (START + step * STEP - before).strftime("%Y-%m-%dT%H:%M:%SZ")


def _article(article: Article) -> dict[str, Any]:
    source = {"name": article.outlet}
    return {"link": article.link, "title": article.title, "source": source,
            "iso_date": _when(article.at, timedelta(hours=3))}  # fmt: skip


def _review(brand: StoryBrand, review: Review) -> dict[str, Any]:
    key = f"{brand.slug}-{review.at}-{review.text}"  # the review's own identity, as Play's id is
    return {"id": review_id(key), "snippet": review.text, "rating": float(review.stars),
            "iso_date": _when(review.at, timedelta(hours=2))}  # fmt: skip


def _related(content: Content, step: int) -> dict[str, Any]:
    shown = [q for q in content.queries if q.at <= step]
    rising = sorted((q for q in shown if q.rising), key=lambda q: -q.at)
    return {
        "rising": [{"query": q.query} for q in rising],
        "top": [{"query": q.query} for q in shown if not q.rising],
    }


def _interest(step: int) -> list[dict[str, Any]]:
    """Thirty days of interest up to the scan's day, VoltBox against SoundNest."""
    today = (START + step * STEP).date()
    days = [today - timedelta(days=n) for n in range(29, -1, -1)]
    return [
        {
            "timestamp": str(int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())),
            "partial_data": d == today,
            "values": [
                {"query": MAIN.name, "query_index": 0, "extracted_value": _voltbox(d)},
                {"query": RIVAL.name, "query_index": 1, "extracted_value": 30 + d.day % 5},
            ],
        }
        for d in days
    ]


def _voltbox(day: date) -> int:
    since = (day - START.date()).days
    crisis = {9: 55, 10: 100, 11: 85, 12: 70, 13: 55}
    return crisis.get(since, 38 + day.day % 4 * 3)


def _tagged(brand: StoryBrand) -> list[tuple[ParsedMention, Tag]]:
    """Each labelled text as the parsers read it, with its tag."""
    c = brand.content
    found: list[tuple[list[ParsedMention], Tag]] = [
        *((parsers.parse_search_page(LANGUAGE | _one_result(r)), r.tag) for r in c.results),
        *(
            (
                parsers.parse_search_page(LANGUAGE | {"related_questions": [{"question": q.text}]}),
                q.tag,
            )
            for q in c.questions
        ),
        *(
            (parsers.parse_news(LANGUAGE | {"news_results": [_article(a)]}), a.tag)
            for a in c.articles
        ),
        *(
            (parsers.parse_play_product(LANGUAGE | {"reviews": [_review(brand, r)]})[1], r.tag)
            for r in c.reviews
        ),
        *((parsers.parse_trends_related(LANGUAGE | _one_query(q)), q.tag) for q in c.queries),
    ]
    return [(mention, tag) for mentions, tag in found for mention in mentions]


def _one_result(result: Result) -> dict[str, Any]:
    item = {"link": result.link, "title": result.title, "snippet": result.snippet, "position": 1}
    return {"organic_results": [item]}


def _one_query(query: Query) -> dict[str, Any]:
    kind = "rising" if query.rising else "top"
    return {"related_queries": {kind: [{"query": query.query}]}}


def _label(mention: ParsedMention, tag: Tag) -> RecordedLabel:
    return RecordedLabel(
        text_id=text_id(mention.source, mention.text),
        prompt_version=LABEL_PROMPT,
        is_about_brand=tag.about,
        sentiment=tag.sentiment,  # type: ignore[arg-type]  # -1, 0 or 1 in the story's tags
        topic=tag.topic,
        is_complaint=tag.complaint,
        severity=tag.severity,
        reason=tag.reason,
    )


def _story(mention: ParsedMention, tag: Tag) -> RecordedNarrative:
    label = tag.story or ""
    return RecordedNarrative(
        text_id=text_id(mention.source, mention.text),
        prompt_version=GROUP_PROMPT,
        label=label,
        summary=STORIES[label],
    )


def _explanations() -> tuple[RecordedExplanation, ...]:
    return tuple(
        RecordedExplanation(
            rule=rule,
            level=level,
            story_label=story,
            prompt_version=EXPLAIN_PROMPT,
            text=said,
        )
        for rule, level, story, said in words.EXPLANATIONS
    )


def _drafts(tagged: Sequence[tuple[ParsedMention, Tag]]) -> tuple[RecordedDraft, ...]:
    """Each draft cites the texts of the story it answers."""
    cites = {
        story: tuple(text_id(m.source, m.text) for m, tag in tagged if tag.story == story)
        for story in STORIES
    }
    return tuple(
        RecordedDraft(
            story_label=story, kind=kind, prompt_version=DRAFT_PROMPT, text=text, cites=cites[story]
        )
        for story, kind, text in words.DRAFTS
    )
