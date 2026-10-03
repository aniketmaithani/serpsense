"""Which task labels which source of mention (data-model §6, BUILD_PLAN §7.1).

Autocomplete suggestions are short enough to get a prompt of their own; everything else,
Google's AI Overview included, is labelled by `label_mentions`. A source whose task has no
prompt yet stays unlabelled, and the scores that read it are partial.
"""

from collections.abc import Mapping

from serpsense.domain.enums import LlmTask, MentionSource

LABELLERS: Mapping[MentionSource, LlmTask] = {
    source: LlmTask.LABEL_MENTIONS for source in MentionSource
} | {MentionSource.AUTOCOMPLETE: LlmTask.CLASSIFY_AUTOCOMPLETE}


# The active prompt of each labelling task that has one (prompts/<task>/v<N>.md); a new version
# labels again, and scoring prefers its labels (docs/scoring.md).
PROMPTS: Mapping[LlmTask, str] = {LlmTask.LABEL_MENTIONS: "label_mentions/v1"}


def sources_for(task: LlmTask) -> frozenset[MentionSource]:
    """The sources a labelling task labels; empty for a task that labels none."""
    return frozenset(source for source, labeller in LABELLERS.items() if labeller is task)
