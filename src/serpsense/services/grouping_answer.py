"""What the grouping prompt is sent and how its answer is read (prompts/group_narratives/v1.md).

Open narratives and waiting mentions go out as records under short ids (n1, m1, …). The answer
is untrusted model output, parsed once here. A label or summary with control or bidi
characters makes the whole answer invalid (`LlmOutputRejected` at the gateway): nothing like that
may reach the database or an alert email, and an answer that holds it is suspect. Everything
else that can't be used is set aside alone, its mentions left waiting:
- a new story longer than the table holds, with every placement in it;
- a placement repeated (the first counts) or naming an unknown mention or narrative;
- a mention with no placement at all.
`Placed.dropped` counts the mentions set aside and `Placed.ignored` the answers dropped, so the
grouper can log them and the eval can report them.
"""

import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from pydantic import AfterValidator, BaseModel, StringConstraints

from serpsense.ports.narrative_store import (
    MAX_LABEL,
    MAX_SUMMARY,
    Assignment,
    NewNarrative,
    OpenNarrative,
    UngroupedMention,
)

# Bidi embeddings, overrides, isolates and marks: they can reorder what a reader sees.
BIDI = frozenset(map(chr, [*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F]))


def _plain(allowed: str) -> AfterValidator:
    def check(text: str) -> str:
        bad = (c for c in text if c not in allowed)
        if any(unicodedata.category(c) == "Cc" or c in BIDI for c in bad):
            raise ValueError("control or bidi characters")
        return text

    return AfterValidator(check)


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Started(BaseModel):
    """A narrative the model starts: a one-line label, and a summary that may break lines."""

    id: str
    label: Annotated[Text, _plain("")]
    summary: Annotated[Text, _plain("\n")]


class Placement(BaseModel):
    id: str
    narrative: str | None  # an open narrative's id, a new one's, or None for a one-off


class Grouping(BaseModel):
    new_narratives: list[Started]
    placements: list[Placement]


@dataclass(frozen=True)
class Placed:
    narratives: list[NewNarrative]  # only those a mention joined
    assignments: list[Assignment]
    one_offs: list[str]  # the mentions' keys placed in no narrative
    dropped: int  # mentions set aside: no placement, an unknown narrative, a story too long
    ignored: int  # placements repeated or naming an unknown mention


def placements(
    grouping: Grouping,
    brand_id: uuid.UUID,
    known: Mapping[str, OpenNarrative],
    mentions: Mapping[str, UngroupedMention],
) -> Placed:
    """What to store from an answer, and what was set aside."""
    new: dict[str, NewNarrative] = {}
    for story in grouping.new_narratives:
        long = len(story.label) > MAX_LABEL or len(story.summary) > MAX_SUMMARY
        if story.id not in known and story.id not in new and not long:
            new[story.id] = NewNarrative(uuid.uuid4(), brand_id, story.label, story.summary)
    targets = {key: story.narrative_id for key, story in known.items()}
    targets |= {key: story.narrative_id for key, story in new.items()}
    assignments: list[Assignment] = []
    one_offs: list[str] = []
    seen: set[str] = set()
    for placement in grouping.placements:
        if placement.id not in mentions or placement.id in seen:
            continue  # counted in `ignored`
        seen.add(placement.id)
        if placement.narrative is None:
            one_offs.append(placement.id)
        elif placement.narrative in targets:  # else the mention keeps waiting
            mention_id = mentions[placement.id].mention_id
            assignments.append(Assignment(mention_id, targets[placement.narrative]))
    used = {a.narrative_id for a in assignments}
    return Placed(
        narratives=[n for n in new.values() if n.narrative_id in used],
        assignments=assignments,
        one_offs=one_offs,
        dropped=len(mentions) - len(assignments) - len(one_offs),
        ignored=len(grouping.placements) - len(seen),
    )


def narrative_record(key: str, story: OpenNarrative) -> dict[str, str]:
    return {"id": key, "mentions": str(story.mentions), "label": story.label, "text": story.summary}


def mention_record(key: str, mention: UngroupedMention) -> dict[str, str]:
    record = {
        "id": key,
        "source": mention.source.value,
        "topic": mention.topic.value,
        "severity": str(mention.severity),
        "text": mention.text,
    }
    if mention.language_code:
        record["language"] = mention.language_code
    return record
