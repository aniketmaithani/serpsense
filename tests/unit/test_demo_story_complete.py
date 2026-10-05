"""The demo story answers every search its brands' scans make, at every scan and after (Scan
now), labels every text those answers show, and records output at the prompt versions the app
asks with: no database needed."""

from collections import deque
from datetime import timedelta

import pytest

from serpsense.adapters.replay.recording import Recording, text_id
from serpsense.adapters.replay.story.build import (
    BRANDS,
    DRAFT_PROMPT,
    EXPLAIN_PROMPT,
    GROUP_PROMPT,
    LABEL_PROMPT,
    StoryBrand,
    recordings,
    target,
)
from serpsense.adapters.serp.collectors import COLLECTORS
from serpsense.adapters.serp.replay import ReplaySearchProvider
from serpsense.domain.enums import LlmTask
from serpsense.domain.labelling import sources_for
from serpsense.ports.collector import Collector, Lead
from serpsense.services import drafts, explanations, grouping, labelling
from tests.fakes import FixedClock

pytestmark = pytest.mark.unit

RECORDINGS = recordings()
BY_SLUG = {recording.brand: recording for recording in RECORDINGS}
LABELLED = sources_for(LlmTask.LABEL_MENTIONS)


def unlabelled(brand: StoryBrand, clock: FixedClock) -> list[str]:
    """Each search the scan makes is answered (SearchFailed otherwise); the texts it would
    send for labelling that the story has no label for."""
    recording: Recording = BY_SLUG[brand.slug]
    labels = {(label.text_id, label.prompt_version) for label in recording.labels}
    provider = ReplaySearchProvider(RECORDINGS, clock)
    leads: deque[tuple[Collector, Lead]] = deque(
        (collector, lead) for collector in COLLECTORS for lead in collector.leads(target(brand))
    )
    missing: list[str] = []
    while leads:
        collector, lead = leads.popleft()
        reading = collector.read(lead, provider.search(lead.request).payload)
        leads.extend((collector, follow_up) for follow_up in reading.follow_ups)
        missing += [
            mention.text
            for mention in reading.mentions
            if mention.source in LABELLED
            and (text_id(mention.source, mention.text), LABEL_PROMPT) not in labels
        ]
    return missing


@pytest.mark.parametrize("brand", BRANDS, ids=lambda brand: brand.slug)
def test_every_scan_is_answered_and_labelled_at_its_time(brand: StoryBrand) -> None:
    for scan in BY_SLUG[brand.slug].scans:
        assert unlabelled(brand, FixedClock(scan.recorded_at + timedelta(milliseconds=1))) == []


@pytest.mark.parametrize("brand", BRANDS, ids=lambda brand: brand.slug)
def test_scan_now_after_the_load_is_answered_and_labelled(brand: StoryBrand) -> None:
    later = FixedClock(BY_SLUG[brand.slug].scans[-1].recorded_at + timedelta(days=30))
    assert unlabelled(brand, later) == []


def test_the_story_speaks_with_the_prompts_the_app_asks_with() -> None:
    assert LABEL_PROMPT == labelling.PROMPT_VERSION
    assert GROUP_PROMPT == grouping.PROMPT_VERSION
    assert EXPLAIN_PROMPT == explanations.PROMPT_VERSION
    assert DRAFT_PROMPT == drafts.PROMPT_VERSION


def test_building_it_twice_gives_the_same_story() -> None:
    assert recordings() == RECORDINGS  # fixed dates: a second load plays nothing new


def test_every_review_keeps_an_id_of_its_own() -> None:
    for recording in RECORDINGS:
        ids = {r["id"] for p in recording.payloads for r in p.get("reviews", [])}
        texts = {r["snippet"] for p in recording.payloads for r in p.get("reviews", [])}
        assert len(ids) == len(texts)  # one id per review, none shared
