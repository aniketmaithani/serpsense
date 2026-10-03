"""The recordings the package ships answer every search the replay demo makes, at every recorded
scan and after (Scan now), and label every text those answers show: no database needed."""

import uuid
from collections import deque
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest

from serpsense.adapters.replay.recording import Recording, load, text_id
from serpsense.adapters.serp.collectors import COLLECTORS
from serpsense.adapters.serp.replay import ReplaySearchProvider
from serpsense.domain.enums import LlmTask
from serpsense.domain.labelling import PROMPTS, sources_for
from serpsense.domain.settings.search import resolve
from serpsense.ports.collector import App, Collector, Lead, Subject, Target
from serpsense.services.demo import DEMO_BRANDS, OLA, RIVALS, DemoBrand
from tests.fakes import FixedClock

pytestmark = pytest.mark.unit

RECORDINGS = load()
BY_SLUG = {recording.brand: recording for recording in RECORDINGS}
LABELLED = sources_for(LlmTask.LABEL_MENTIONS)
PROMPT = PROMPTS[LlmTask.LABEL_MENTIONS]


def target(demo: DemoBrand, settings: Mapping[str, Any]) -> Target:
    """The demo brand as a scan aims it: Ola tracks the four rivals, ordered by name as the scan
    target reads them; every brand has its Play app. As seed_demo(replay=...) seeds it, the
    brand's settings are its recorded ones and nothing else is resolved over them."""
    rivals = sorted(RIVALS, key=lambda rival: rival.name) if demo is OLA else []
    return Target(
        Subject(uuid.uuid4(), demo.name),
        resolve(settings),
        competitors=tuple(Subject(uuid.uuid4(), rival.name) for rival in rivals),
        apps=(App(uuid.uuid4(), demo.app),),
    )


def unlabelled(demo: DemoBrand, settings: Mapping[str, Any], clock: FixedClock) -> list[str]:
    """Each search the scan makes is answered (SearchFailed otherwise); the texts it would
    send for labelling that the recording has no label for."""
    recording: Recording = BY_SLUG[demo.slug]
    labels = {(label.text_id, label.prompt_version) for label in recording.labels}
    provider = ReplaySearchProvider(RECORDINGS, clock)
    aim = target(demo, settings)
    leads: deque[tuple[Collector, Lead]] = deque(
        (collector, lead) for collector in COLLECTORS for lead in collector.leads(aim)
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
            and (text_id(mention.source, mention.text), PROMPT) not in labels
        ]
    return missing


@pytest.mark.parametrize("demo", DEMO_BRANDS, ids=lambda demo: demo.slug)
def test_every_recorded_scan_is_answered_and_labelled_at_its_time(demo: DemoBrand) -> None:
    for scan in BY_SLUG[demo.slug].scans:
        clock = FixedClock(scan.recorded_at + timedelta(milliseconds=1))
        assert unlabelled(demo, scan.settings, clock) == []


@pytest.mark.parametrize("demo", DEMO_BRANDS, ids=lambda demo: demo.slug)
def test_scan_now_after_the_load_is_answered_and_labelled(demo: DemoBrand) -> None:
    latest = BY_SLUG[demo.slug].scans[-1]  # the settings seed_demo(replay=...) gives the brand
    later = FixedClock(latest.recorded_at + timedelta(days=30))
    assert unlabelled(demo, latest.settings, later) == []
