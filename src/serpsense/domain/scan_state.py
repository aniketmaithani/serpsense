"""The scan lifecycle (data-model §4, AGENTS §5): the only transitions a scan may make.

The database enforces the same table (`ck_scan_status_transitions_allowed_transition`), and a
parity test keeps the two in step. Every status change is recorded as a transition with a
machine reason that fits it; anything not declared here raises IllegalTransition.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from serpsense.domain.enums import ScanStatus, TransitionActor


class TransitionReason(StrEnum):
    """Why a scan changed status (`scan_status_transitions.reason`)."""

    SCHEDULED = "scheduled"  # created for a schedule slot
    REQUESTED = "requested"  # created by "Scan now"
    REPLAYED = "replayed"  # created by the seed/replay CLI
    CLAIMED = "claimed"  # a worker started it
    COMPLETED = "completed"  # every surface succeeded or had nothing to show
    SURFACES_FAILED = "surfaces_failed"  # some surfaces failed: partial
    ENRICHMENT_FAILED = "enrichment_failed"  # the LLM failed: partial, deterministic scores
    ALL_SURFACES_FAILED = "all_surfaces_failed"
    STAGE_FAILED = "stage_failed"  # a pipeline stage raised an unexpected error
    TIMED_OUT = "timed_out"  # the sweep found it running past the time limit
    BUDGET_EXHAUSTED = "budget_exhausted"
    QUOTA_INSUFFICIENT = "quota_insufficient"  # SerpApi's own quota
    BRAND_ARCHIVED = "brand_archived"
    ACCOUNT_DELETED = "account_deleted"


S, R = ScanStatus, TransitionReason
# Every transition a scan may make, with the reasons it may be made for.
REASONS: Mapping[tuple[ScanStatus | None, ScanStatus], frozenset[TransitionReason]] = {
    (None, S.QUEUED): frozenset({R.SCHEDULED, R.REQUESTED, R.REPLAYED}),
    (S.QUEUED, S.RUNNING): frozenset({R.CLAIMED}),
    (S.QUEUED, S.SKIPPED): frozenset({R.BRAND_ARCHIVED, R.ACCOUNT_DELETED}),
    (S.RUNNING, S.SUCCEEDED): frozenset({R.COMPLETED}),
    (S.RUNNING, S.PARTIAL): frozenset({R.SURFACES_FAILED, R.ENRICHMENT_FAILED}),
    (S.RUNNING, S.FAILED): frozenset({R.ALL_SURFACES_FAILED, R.STAGE_FAILED, R.TIMED_OUT}),
    (S.RUNNING, S.SKIPPED): frozenset(
        {R.BUDGET_EXHAUSTED, R.QUOTA_INSUFFICIENT, R.BRAND_ARCHIVED, R.ACCOUNT_DELETED}
    ),
}
ALLOWED: Mapping[ScanStatus | None, frozenset[ScanStatus]] = {
    origin: frozenset(to for (start, to) in REASONS if start == origin)
    for origin in {start for start, _ in REASONS}
}
ACTIVE = frozenset({S.QUEUED, S.RUNNING})
FINAL = frozenset(ScanStatus) - ACTIVE


class IllegalTransition(ValueError):
    """A status change the lifecycle doesn't allow."""


@dataclass(frozen=True)
class Transition:
    from_status: ScanStatus | None
    to_status: ScanStatus
    reason: TransitionReason
    actor: TransitionActor = TransitionActor.SYSTEM
    actor_user_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        reasons = REASONS.get((self.from_status, self.to_status))
        if reasons is None:
            raise IllegalTransition(f"{self.from_status} → {self.to_status}")
        if self.reason not in reasons:
            raise IllegalTransition(f"{self.from_status} → {self.to_status} for {self.reason}")
        if (self.actor is TransitionActor.USER) != (self.actor_user_id is not None):
            raise ValueError("a user transition names its user, and only a user transition does")


def is_final(status: ScanStatus) -> bool:
    return status in FINAL
