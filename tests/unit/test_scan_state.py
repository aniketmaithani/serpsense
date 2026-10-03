"""The scan lifecycle: only declared transitions, with a machine reason (AGENTS §5)."""

import re
import uuid

import pytest

from serpsense.domain.enums import ScanStatus, SurfaceOutcome, TransitionActor
from serpsense.domain.scan_state import (
    ACTIVE,
    FINAL,
    REASONS,
    IllegalTransition,
    Transition,
    TransitionReason,
    finished,
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


SUCCEEDED, NOT_SHOWN = SurfaceOutcome.SUCCEEDED, SurfaceOutcome.NOT_SHOWN
FAILED, CIRCUIT = SurfaceOutcome.FAILED, SurfaceOutcome.CIRCUIT_OPEN
SPENT, DISABLED = SurfaceOutcome.BUDGET_EXHAUSTED, SurfaceOutcome.DISABLED


E = TransitionReason


@pytest.mark.parametrize(
    ("surfaces", "answered", "enrichment_failed", "ending"),
    [
        ([SUCCEEDED, NOT_SHOWN, DISABLED], True, False, (ScanStatus.SUCCEEDED, E.COMPLETED)),
        ([SUCCEEDED, NOT_SHOWN], True, True, (ScanStatus.PARTIAL, E.ENRICHMENT_FAILED)),
        ([SUCCEEDED, FAILED], True, True, (ScanStatus.PARTIAL, E.SURFACES_FAILED)),
        ([NOT_SHOWN, SPENT, DISABLED], False, False, (ScanStatus.PARTIAL, E.SURFACES_FAILED)),
        ([FAILED, SPENT], True, False, (ScanStatus.PARTIAL, E.SURFACES_FAILED)),  # some came back
        (
            [FAILED, CIRCUIT, SPENT, DISABLED],
            False,
            False,
            (ScanStatus.FAILED, E.ALL_SURFACES_FAILED),
        ),
        ([CIRCUIT], False, False, (ScanStatus.FAILED, E.ALL_SURFACES_FAILED)),
        ([SPENT, SPENT, DISABLED], False, False, (ScanStatus.SKIPPED, E.BUDGET_EXHAUSTED)),
        ([DISABLED], False, False, (ScanStatus.SUCCEEDED, E.COMPLETED)),
    ],
)
def test_a_running_scan_ends_by_how_its_surfaces_went(
    surfaces: list[SurfaceOutcome],
    answered: bool,
    enrichment_failed: bool,
    ending: tuple[ScanStatus, TransitionReason],
) -> None:
    done = finished(iter(surfaces), answered=answered, enrichment_failed=enrichment_failed)
    assert (done.from_status, done.to_status, done.reason) == (ScanStatus.RUNNING, *ending)
