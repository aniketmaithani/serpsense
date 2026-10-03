"""Response drafts and their citations: model output, append-only (data-model §6)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import DraftKind, DraftPreset


def _fk(target: str) -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{target}.id", ondelete="RESTRICT"))


def _from_task(task: str) -> CheckConstraint:
    return CheckConstraint(
        f"split_part(prompt_version, '/', 1) = '{task}'", name=f"prompt_from_{task}_task"
    )


def _length(column: str, most: int) -> CheckConstraint:
    return CheckConstraint(
        f"{column} ~ '\\S' AND char_length({column}) <= {most}", name=f"{column}_length"
    )


class Draft(Base):
    """A response a person may copy, for a narrative; asking again makes a new draft. From a
    successful draft_response call made for the narrative's brand's owner, who asked for it."""

    __tablename__ = "drafts"
    __table_args__ = (
        Index("ix_drafts_narrative_id_created_at", "narrative_id", "created_at"),
        UniqueConstraint("llm_call_id"),  # one draft per call
        _length("text", 4000),
        CheckConstraint(
            "reasoning_summary IS NULL OR char_length(reasoning_summary) <= 8000",
            name="reasoning_summary_length",
        ),
        _from_task("draft_response"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    narrative_id: Mapped[uuid.UUID] = _fk("narratives")
    kind: Mapped[DraftKind] = mapped_column(pg_enum(DraftKind, "draft_kind"))
    preset: Mapped[DraftPreset] = mapped_column(pg_enum(DraftPreset, "draft_preset"))
    text: Mapped[str] = mapped_column(Text)
    reasoning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str] = mapped_column(Text)
    llm_call_id: Mapped[uuid.UUID] = _fk("llm_calls")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class DraftCitation(Base):
    """A mention a draft rests on, of the draft's brand (trigger)."""

    __tablename__ = "draft_citations"
    __table_args__ = (Index("ix_draft_citations_mention_id", "mention_id"),)

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drafts.id", ondelete="RESTRICT"), primary_key=True
    )
    mention_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mentions.id", ondelete="RESTRICT"), primary_key=True
    )
