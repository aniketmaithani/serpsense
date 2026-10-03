"""Which task labels which source (data-model §6)."""

import pytest

from serpsense.domain.enums import LlmTask, MentionSource
from serpsense.domain.labelling import LABELLERS, sources_for

pytestmark = pytest.mark.unit


def test_every_source_has_one_labeller() -> None:
    assert set(LABELLERS) == set(MentionSource)
    assert sources_for(LlmTask.CLASSIFY_AUTOCOMPLETE) == {MentionSource.AUTOCOMPLETE}
    assert sources_for(LlmTask.ASSESS_AI_OVERVIEW) == {MentionSource.AI_OVERVIEW}
    general = sources_for(LlmTask.LABEL_MENTIONS)
    assert MentionSource.PLAY_REVIEW in general and MentionSource.AUTOCOMPLETE not in general
    assert sources_for(LlmTask.DRAFT_RESPONSE) == frozenset()
