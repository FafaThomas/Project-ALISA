import pytest

from coding_agent.change_tracking import (
    ChangeState,
    WorkspaceChange,
    content_hash,
)
from coding_agent.models import ChangeType, FileChange
from coding_agent.workspace import Workspace
from coding_agent.write_guard import (
    ApprovalRequiredError,
    StaleFileError,
    WriteGuardError,
    validate_approved_change,
)


def build_pair(root, *, approved=True):
    file_path = root / "example.py"
    original = b"VALUE = 1\n"
    desired = "VALUE = 2\n"
    file_path.write_bytes(original)

    proposal = FileChange(
        path="example.py",
        change_type=ChangeType.MODIFY,
        reason="Update value",
        content=desired,
    )
    record = WorkspaceChange(
        change_id="change-001",
        relative_path="example.py",
        operation="modify",
        state=ChangeState.APPROVED if approved else ChangeState.PROPOSED,
        original_hash=content_hash(original),
        resulting_hash=content_hash(desired.encode("utf-8")),
        reason="Update value",
    )
    return Workspace(root), record, proposal, file_path


def test_approved_current_change_is_authorized(tmp_path):
    workspace, record, proposal, _ = build_pair(tmp_path)

    authorization = validate_approved_change(workspace, record, proposal)

    assert authorization.change_id == record.change_id
    assert authorization.expected_original_hash == record.original_hash
    assert authorization.intended_result_hash == record.resulting_hash


def test_unapproved_change_is_rejected(tmp_path):
    workspace, record, proposal, _ = build_pair(tmp_path, approved=False)

    with pytest.raises(ApprovalRequiredError):
        validate_approved_change(workspace, record, proposal)


def test_changed_file_is_rejected_as_stale(tmp_path):
    workspace, record, proposal, file_path = build_pair(tmp_path)
    file_path.write_text("VALUE = 999\n", encoding="utf-8")

    with pytest.raises(StaleFileError, match="differs"):
        validate_approved_change(workspace, record, proposal)


def test_missing_original_file_is_rejected(tmp_path):
    workspace, record, proposal, file_path = build_pair(tmp_path)
    file_path.unlink()

    with pytest.raises(StaleFileError):
        validate_approved_change(workspace, record, proposal)


def test_proposal_path_must_match_record(tmp_path):
    workspace, record, proposal, _ = build_pair(tmp_path)
    other_proposal = FileChange(
        path="other.py",
        change_type=ChangeType.MODIFY,
        reason="Different file",
        content="VALUE = 2\n",
    )

    with pytest.raises(WriteGuardError, match="paths differ"):
        validate_approved_change(workspace, record, other_proposal)


def test_proposal_content_must_match_recorded_hash(tmp_path):
    workspace, record, _, _ = build_pair(tmp_path)
    wrong_proposal = FileChange(
        path="example.py",
        change_type=ChangeType.MODIFY,
        reason="Different content",
        content="VALUE = 3\n",
    )

    with pytest.raises(WriteGuardError, match="resulting hash"):
        validate_approved_change(workspace, record, wrong_proposal)


def test_create_rejects_existing_target(tmp_path):
    (tmp_path / "new.py").write_text("already exists", encoding="utf-8")
    workspace = Workspace(tmp_path)
    content = "VALUE = 1\n"

    proposal = FileChange(
        path="new.py",
        change_type=ChangeType.CREATE,
        reason="Create file",
        content=content,
    )
    record = WorkspaceChange(
        change_id="create-001",
        relative_path="new.py",
        operation="create",
        state=ChangeState.APPROVED,
        original_hash=None,
        resulting_hash=content_hash(content.encode("utf-8")),
        reason="Create file",
    )

    with pytest.raises(StaleFileError, match="already exists"):
        validate_approved_change(workspace, record, proposal)


def test_guard_does_not_modify_file(tmp_path):
    workspace, record, proposal, file_path = build_pair(tmp_path)
    original = file_path.read_bytes()

    validate_approved_change(workspace, record, proposal)

    assert file_path.read_bytes() == original