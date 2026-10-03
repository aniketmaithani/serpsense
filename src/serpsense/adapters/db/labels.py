"""Which of a mention's labels counts (data-model §6, docs/scoring.md): one rule for every reader.

A mention's current label is one of its latest revision's, from its own labelling task
(`domain.labelling.LABELLERS`): under the task's active prompt, or, until the mention is labelled
again, the newest under an earlier one, so a new prompt version doesn't reset what is known.
Readers join `enrichments` to `mentions`, add `conditions`, and take the first row per mention
in `preference` order (`DISTINCT ON (mentions.id)`).
"""

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import ColumnElement, Table, func, select, tuple_

from serpsense.adapters.db.models.llm import Enrichment
from serpsense.adapters.db.models.mentions import Mention, MentionRevision
from serpsense.domain.enums import LlmTask
from serpsense.domain.labelling import LABELLERS

ENRICHMENTS = cast(Table, Enrichment.__table__)
MENTIONS = cast(Table, Mention.__table__)
REVISIONS = cast(Table, MentionRevision.__table__)
OWN_TASK = [(source, task.value) for source, task in LABELLERS.items()]


def latest_revision() -> ColumnElement[int]:
    """The mention's latest revision number; 1 is its own text."""
    return (
        select(func.coalesce(func.max(REVISIONS.c.revision), 1))
        .where(REVISIONS.c.mention_id == MENTIONS.c.id)
        .scalar_subquery()
    )


def conditions() -> list[ColumnElement[bool]]:
    """An enrichment row that may be the mention's current label."""
    task = func.split_part(ENRICHMENTS.c.prompt_version, "/", 1)
    return [
        ENRICHMENTS.c.revision == latest_revision(),
        tuple_(MENTIONS.c.source, task).in_(OWN_TASK),
    ]


def preference(prompts: Mapping[LlmTask, str]) -> list[ColumnElement[Any]]:
    """The order after `mentions.id` that puts the current label first."""
    active = ENRICHMENTS.c.prompt_version.in_(list(prompts.values()))
    return [active.desc(), ENRICHMENTS.c.created_at.desc(), ENRICHMENTS.c.id]
