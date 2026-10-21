
from coding_agent.recovery import (
    RecoveryAction,
    RecoveryCandidate,
    RecoveryStatus,
    decide_recovery,
)


def make_candidate(
    *,
    event="apply_intent",
    status=RecoveryStatus.ORIGINAL,
    operation="modify",
    recovery_bytes=b"original contents",
):
    return RecoveryCandidate(
        change_id="decision-test",
        relative_path="example.txt",
        operation=operation,
        last_event=event,
        state="approved",
        original_hash="original-hash",
        resulting_hash="result-hash",
        recovery_bytes=recovery_bytes,
        assessment="test assessment",
        status=status,
        current_hash="current-hash",
    )


def test_unapplied_intent_is_aborted_not_replayed():
    decision = decide_recovery(make_candidate())

    assert decision.action == RecoveryAction.ABORT_UNAPPLIED


def test_applied_intent_can_be_reconciled():
    candidate = make_candidate(status=RecoveryStatus.APPLIED)

    decision = decide_recovery(candidate)

    assert decision.action == RecoveryAction.FINALIZE_APPLY


def test_applied_change_needs_verification():
    candidate = make_candidate(
        event="applied",
        status=RecoveryStatus.APPLIED,
    )

    decision = decide_recovery(candidate)

    assert decision.action == RecoveryAction.VERIFY


def test_unexpected_contents_require_manual_intervention():
    candidate = make_candidate(status=RecoveryStatus.CONFLICT)

    decision = decide_recovery(candidate)

    assert decision.action == RecoveryAction.MANUAL_INTERVENTION


def test_completed_rollback_can_be_reconciled():
    candidate = make_candidate(
        event="rollback_intent",
        status=RecoveryStatus.ORIGINAL,
    )

    decision = decide_recovery(candidate)

    assert decision.action == RecoveryAction.FINALIZE_ROLLBACK


def test_pending_rollback_requires_recovery_data():
    candidate = make_candidate(
        event="rollback_intent",
        status=RecoveryStatus.APPLIED,
        recovery_bytes=None,
    )

    decision = decide_recovery(candidate)

    assert decision.action == RecoveryAction.MANUAL_INTERVENTION


def test_created_file_can_be_removed_without_original_bytes():
    candidate = make_candidate(
        event="rollback_intent",
        status=RecoveryStatus.APPLIED,
        operation="create",
        recovery_bytes=None,
    )

    decision = decide_recovery(candidate)

    assert decision.action == RecoveryAction.RESTORE_ORIGINAL
