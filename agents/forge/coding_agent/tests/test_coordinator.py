import pytest

from coding_agent.change_tracking import (
    ChangeState,
    WorkspaceChange,
    content_hash,
)
from coding_agent.coordinator import ChangeCoordinator
from coding_agent.models import ChangeType, FileChange
from coding_agent.workspace import Workspace


def make_change(path, operation, original, desired, *, state=ChangeState.APPROVED):
    return WorkspaceChange(
        change_id=f"coordinator-{operation}",
        relative_path=path,
        operation=operation,
        state=state,
        original_hash=content_hash(original),
        resulting_hash=content_hash(desired),
        reason="Coordinator integration test",
    )


def make_proposal(path, operation, desired):
    return FileChange(
        path=path,
        change_type=ChangeType(operation),
        reason="Coordinator integration test",
        content=desired.decode("utf-8") if desired is not None else None,
    )


def test_apply_succeeds_and_retains_recovery(tmp_path):
    desired = b"VALUE = 1\n"
    coordinator = ChangeCoordinator(Workspace(tmp_path))
    change = make_change("new.py", "create", None, desired)
    proposal = make_proposal("new.py", "create", desired)

    result = coordinator.apply(change, proposal)

    assert result.change.state == ChangeState.APPLIED
    assert result.recovery is not None
    assert (tmp_path / "new.py").read_bytes() == desired


def test_verify_succeeds_when_result_hash_matches(tmp_path):
    desired = b"VALUE = 2\n"
    (tmp_path / "example.py").write_bytes(desired)
    coordinator = ChangeCoordinator(Workspace(tmp_path))
    change = make_change("example.py", "modify", b"VALUE = 1\n", desired)
    applied = change.transition(ChangeState.APPLIED)

    from coding_agent.coordinator import CoordinatedChange
    result = coordinator.verify(CoordinatedChange(applied))

    assert result.change.state == ChangeState.VERIFIED
    assert result.change.error is None


def test_verify_fails_when_file_was_changed_externally(tmp_path):
    desired = b"VALUE = 2\n"
    (tmp_path / "example.py").write_bytes(b"EXTERNAL CHANGE\n")
    coordinator = ChangeCoordinator(Workspace(tmp_path))
    change = make_change("example.py", "modify", b"VALUE = 1\n", desired)
    applied = change.transition(ChangeState.APPLIED)

    from coding_agent.coordinator import CoordinatedChange
    result = coordinator.verify(CoordinatedChange(applied))

    assert result.change.state == ChangeState.FAILED
    assert "hash differs" in result.change.error


def test_rollback_succeeds_after_apply_and_verify(tmp_path):
    original = b"VALUE = 1\n"
    desired = b"VALUE = 2\n"
    (tmp_path / "example.py").write_bytes(original)
    coordinator = ChangeCoordinator(Workspace(tmp_path))
    change = make_change("example.py", "modify", original, desired)
    proposal = make_proposal("example.py", "modify", desired)

    applied = coordinator.apply(change, proposal)
    verified = coordinator.verify(applied)
    rolled_back = coordinator.rollback(verified)

    assert rolled_back.change.state == ChangeState.ROLLED_BACK
    assert (tmp_path / "example.py").read_bytes() == original


def test_write_failure_records_failed_state(tmp_path):
    desired = b"VALUE = 2\n"
    (tmp_path / "example.py").write_bytes(b"EXTERNAL\n")
    coordinator = ChangeCoordinator(Workspace(tmp_path))
    change = make_change("example.py", "modify", b"VALUE = 1\n", desired)
    proposal = make_proposal("example.py", "modify", desired)

    result = coordinator.apply(change, proposal)

    assert result.change.state == ChangeState.FAILED
    assert result.change.error


def test_unapproved_change_cannot_be_applied(tmp_path):
    desired = b"VALUE = 1\n"
    coordinator = ChangeCoordinator(Workspace(tmp_path))
    change = make_change(
        "new.py", "create", None, desired, state=ChangeState.PROPOSED
    )
    proposal = make_proposal("new.py", "create", desired)

    with pytest.raises(ValueError, match="approved"):
        coordinator.apply(change, proposal)

    assert not (tmp_path / "new.py").exists()


def test_failed_verification_can_be_rolled_back(tmp_path):
    from coding_agent.coordinator import CoordinatedChange

    original = b"VALUE = 1\n"
    desired = b"VALUE = 2\n"
    target = tmp_path / "example.py"
    target.write_bytes(desired)

    coordinator = ChangeCoordinator(Workspace(tmp_path))
    change = make_change("example.py", "modify", original, desired)
    failed = change.transition(ChangeState.FAILED, error="Verification failed")

    recovery = __import__(
        "coding_agent.workspace_writer",
        fromlist=["RecoveryRecord"],
    ).RecoveryRecord(
        change_id=change.change_id,
        relative_path="example.py",
        operation="modify",
        original_bytes=original,
        resulting_hash=content_hash(desired),
    )

    result = coordinator.rollback(CoordinatedChange(failed, recovery))

    assert result.change.state == ChangeState.ROLLED_BACK
    assert target.read_bytes() == original
