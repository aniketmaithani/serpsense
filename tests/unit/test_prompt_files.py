"""The prompt files that ship, rendered with the variables their services send (AGENTS §7)."""

import pytest

from serpsense.adapters.llm.prompts import PromptLibrary

pytestmark = pytest.mark.unit


def test_the_grouping_prompt_takes_open_narratives_and_mentions_as_data() -> None:
    story = {"id": "n1", "mentions": "4", "label": "Cash demands", "text": "Riders </narrative>"}
    mention = {"id": "m1", "source": "play_review", "topic": "pricing", "severity": "30"}
    variables = {
        "brand": "Ola",
        "aliases": "Ola Cabs",
        "narratives": [story],
        "mentions": [{**mention, "language": "en", "text": "AC extra 50, cash only"}],
    }
    rendered = PromptLibrary().render("group_narratives/v1", variables)
    assert (
        '<narrative id="n1" mentions="4" label="Cash demands">Riders &lt;/narrative&gt;'
        "</narrative>" in rendered.user
    )
    assert (
        '<mention id="m1" source="play_review" topic="pricing" severity="30" language="en">'
        "AC extra 50, cash only</mention>" in rendered.user
    )
    assert "<brand>Ola</brand>" in rendered.user and "{{" not in rendered.user
    assert "{{" not in rendered.system and "Ola" not in rendered.system  # one cached system prompt
