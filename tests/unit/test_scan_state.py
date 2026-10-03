"""The scan lifecycle: only declared transitions, with a machine reason (AGENTS §5)."""

import re
import uuid

import pytest

from serpsense.domain.enums import ScanStatus, TransitionActor
from serpsense.domain.scan_state import (
    ACTIVE,
    FINAL,
    REASONS,
    IllegalTransition,
    Transition,
    TransitionReason,
    is_final,
)

pytestmark = pytest.mark.unit

S = ScanStatus


DECLARED = [(start, to, reason) for (start, to), reasons in REASONS.items() for reason in reasons]


@pytest.mark.parametrize(("from_status", "to_status", "reason"), DECLARED)
def test_declared_transitions_are_allowed_for_their_reasons(
    from_status: ScanStatus | None, to_status: ScanStatus, reason: TransitionReason
) -> None:
    assert Transition(from_status, to_status, reason).to_status is to_status


def test_the_lifecycle_has_seven_transitions_and_every_reason_is_used() -> None:
    assert len(REASONS) == 7
    assert {reason for _, _, reason in DECLARED} == set(TransitionReason)


@pytest.mark.parametrize(
    ("from_status", "to_status", "reason"),
    [
        (S.QUEUED, S.RUNNING, TransitionReason.COMPLETED),
        (S.QUEUED, S.SKIPPED, TransitionReason.BUDGET_EXHAUSTED),  # checked after the claim
        (S.RUNNING, S.FAILED, TransitionReason.SURFACES_FAILED),  # that one is partial
        (None, S.QUEUED, TransitionReason.CLAIMED),
    ],
)
def test_a_reason_that_doesnt_fit_the_transition_is_illegal(
    from_status: ScanStatus | None, to_status: ScanStatus, reason: TransitionReason
) -> None:
    with pytest.raises(IllegalTransition, match=reason.value):
        Transition(from_status, to_status, reason)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (None, S.RUNNING),  # a scan is created queued
        (S.QUEUED, S.SUCCEEDED),  # it must run first
        (S.RUNNING, S.QUEUED),
        (S.SUCCEEDED, S.RUNNING),  # final states are final
        (S.FAILED, S.QUEUED),
        (S.QUEUED, S.QUEUED),
    ],
)
def test_anything_else_is_illegal(from_status: ScanStatus | None, to_status: ScanStatus) -> None:
    with pytest.raises(IllegalTransition):
        Transition(from_status, to_status, TransitionReason.CLAIMED)


def test_a_user_transition_names_its_user() -> None:
    user = uuid.uuid4()
    requested = Transition(None, S.QUEUED, TransitionReason.REQUESTED, TransitionActor.USER, user)
    assert requested.actor_user_id == user
    with pytest.raises(ValueError, match="user"):
        Transition(None, S.QUEUED, TransitionReason.REQUESTED, TransitionActor.USER)
    with pytest.raises(ValueError, match="user"):
        Transition(None, S.QUEUED, TransitionReason.SCHEDULED, actor_user_id=user)


def test_active_and_final_states_split_the_lifecycle() -> None:
    assert set(ScanStatus) == ACTIVE | FINAL and not ACTIVE & FINAL
    assert [s for s in ScanStatus if is_final(s)] == [S.SUCCEEDED, S.PARTIAL, S.FAILED, S.SKIPPED]


def test_reasons_fit_the_ledger_format() -> None:
    """Every reason passes ck_scan_status_transitions_reason_format."""
    assert all(re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", r.value) for r in TransitionReason)
