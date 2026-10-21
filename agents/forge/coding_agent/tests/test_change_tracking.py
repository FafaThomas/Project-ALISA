import pytest

from src.coding_agent.change_tracking import (
    ChangeState,
    WorkspaceChange,
    content_hash,
)


def make_change(**overrides):
    values = {
        "change_id": "change-001",
        "relative_path": "src/example.py",
        "operation": "modify",
        "reason": "Fix a bug",
        "original_hash": content_hash(b"old"),
        "resulting_hash": content_hash(b"new"),
    }
    values.update(overrides)
    return WorkspaceChange(**values)


def test_content_hash_is_stable():
    assert content_hash(b"hello") == content_hash(b"hello")
    assert content_hash(b"hello") != content_hash(b"world")


def test_none_hash_represents_missing_file():
    assert content_hash(None) is None


def test_new_change_starts_proposed():
    change = make_change()
    assert change.state == ChangeState.PROPOSED


def test_create_requires_missing_original():
    with pytest.raises(ValueError, match="missing file"):
        make_change(operation="create", original_hash=content_hash(b"existing"))


def test_delete_requires_missing_result():
    with pytest.raises(ValueError, match="missing file"):
        make_change(operation="delete", resulting_hash=content_hash(b"still here"))


def test_approval_transition():
    change = make_change()
    approved = change.transition(ChangeState.APPROVED)

    assert change.state == ChangeState.PROPOSED
    assert approved.state == ChangeState.APPROVED


def test_cannot_apply_without_approval():
    with pytest.raises(ValueError, match="Invalid change transition"):
        make_change().transition(ChangeState.APPLIED)


def test_valid_apply_and_verify_transitions():
    change = make_change()
    approved = change.transition(ChangeState.APPROVED)
    applied = approved.transition(ChangeState.APPLIED)
    verified = applied.transition(ChangeState.VERIFIED)

    assert verified.state == ChangeState.VERIFIED


def test_failed_change_records_error():
    failed = make_change().transition(
        ChangeState.FAILED,
        error="Permission denied",
    )

    assert failed.state == ChangeState.FAILED
    assert failed.error == "Permission denied"


def test_verified_change_can_be_rolled_back():
    change = make_change()
    change = change.transition(ChangeState.APPROVED)
    change = change.transition(ChangeState.APPLIED)
    change = change.transition(ChangeState.VERIFIED)
    rolled_back = change.transition(ChangeState.ROLLED_BACK)

    assert rolled_back.state == ChangeState.ROLLED_BACK


def test_terminal_state_cannot_be_reused():
    rolled_back = make_change().transition(ChangeState.FAILED)
    rolled_back = rolled_back.transition(ChangeState.ROLLED_BACK)

    with pytest.raises(ValueError, match="Invalid change transition"):
        rolled_back.transition(ChangeState.APPROVED)