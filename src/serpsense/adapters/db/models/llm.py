"""The LLM call ledger (docs/architecture/data-model.md §6)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, BigInteger, CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import LlmCallOutcome, LlmTask

TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


class LlmCall(Base):
    """Append-only: one row per model call, with ids, counts, cost and settings but never the
    prompt or the completion (ADR-0008). A scan's calls belong to its brand's owner (trigger,
    migration 0016)."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_user_id_created_at", "user_id", "created_at"),  # monthly spend
        Index("ix_llm_calls_scan_id", "scan_id"),
        # A response names the model that served it; a call that failed has none.
        CheckConstraint(
            "(outcome = 'failed') = (served_model IS NULL)", name="served_model_iff_response"
        ),
        CheckConstraint(
            "requested_model ~ '^[a-z0-9][a-z0-9.-]{0,63}$'", name="requested_model_format"
        ),
        CheckConstraint("served_model ~ '^[a-z0-9][a-z0-9.-]{0,63}$'", name="served_model_format"),
        CheckConstraint(
            "prompt_version ~ '^[a-z][a-z_]{0,47}/v[1-9][0-9]{0,3}$'", name="prompt_version_format"
        ),
        # The prompt file belongs to the call's task.
        CheckConstraint(
            "split_part(prompt_version, '/', 1) = task::text", name="prompt_matches_task"
        ),
        CheckConstraint("stop_reason ~ '^[a-z][a-z_]{0,31}$'", name="stop_reason_format"),
        CheckConstraint(
            "jsonb_typeof(request_settings) = 'object'", name="request_settings_is_object"
        ),
        *[
            CheckConstraint(f"{column} >= 0", name=f"{column}_non_negative")
            for column in TOKEN_COLUMNS
        ],
        CheckConstraint("cost_micros >= 0", name="cost_micros_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso_4217"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    # NULL for calls outside a scan, such as a draft.
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="RESTRICT")
    )
    task: Mapped[LlmTask] = mapped_column(pg_enum(LlmTask, "llm_task"))
    requested_model: Mapped[str] = mapped_column(Text)
    # From the response: refusal fallback can serve another model, and cost is priced on it.
    served_model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)  # "<task>/v<N>", the prompt file
    request_settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer)
    cost_micros: Mapped[int] = mapped_column(BigInteger)  # 1 micro = 10⁻⁶ of `currency`
    currency: Mapped[str] = mapped_column(CHAR(3))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[LlmCallOutcome] = mapped_column(pg_enum(LlmCallOutcome, "llm_call_outcome"))
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
