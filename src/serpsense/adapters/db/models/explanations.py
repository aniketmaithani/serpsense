"""Alert explanations: model output, append-only (data-model §6)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base


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


class AlertExplanation(Base):
    """Why an alert fired, in plain words: one per alert, from a successful explain_crisis call
    made for the alert's brand's owner (migration 0025)."""

    __tablename__ = "alert_explanations"
    __table_args__ = (
        UniqueConstraint("alert_id"),
        UniqueConstraint("llm_call_id"),  # a call explains one alert
        _length("text", 2000),
        _from_task("explain_crisis"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    alert_id: Mapped[uuid.UUID] = _fk("alerts")
    text: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    llm_call_id: Mapped[uuid.UUID] = _fk("llm_calls")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
