import pytest

from src.coding_agent.change_tracking import (
    ChangeState,
    WorkspaceChange,
    content_hash,
)
from src.coding_agent.models import ChangeType, FileChange
from src.coding_agent.workspace import Workspace
from src.coding_agent.workspace_writer import (
    RecoveryRecord,
    WorkspaceWriteError,
    WorkspaceWriter,
)


def make_change(path, operation, original, desired, *, approved=True):
    return WorkspaceChange(
        change_id="write-001",
        relative_path=path,
        operation=operation,
        state=ChangeState.APPROVED if approved else ChangeState.PROPOSED,
        original_hash=content_hash(original),
        resulting_hash=content_hash(desired),
        reason="Test write",
    )


def test_creates_new_file(tmp_path):
    workspace = Workspace(tmp_path)
    writer = WorkspaceWriter(workspace)
    desired = b"VALUE = 1\n"

    proposal = FileChange(
        path="new.py",
        change_type=ChangeType.CREATE,
        reason="Create module",
        content=desired.decode(),
    )
    record = make_change("new.py", "create", None, desired)

    result = writer.apply(record, proposal)

    assert result.applied
    assert (tmp_path / "new.py").read_bytes() == desired
    assert result.recovery.original_bytes is None


def test_modifies_existing_file_and_preserves_original(tmp_path):
    target = tmp_path / "example.py"
    original = b"VALUE = 1\n"
    desired = b"VALUE = 2\n"
    target.write_bytes(original)

    writer = WorkspaceWriter(Workspace(tmp_path))
    proposal = FileChange(
        path="example.py",
        change_type=ChangeType.MODIFY,
        reason="Update value",
        content=desired.decode(),
    )
    record = make_change("example.py", "modify", original, desired)

    result = writer.apply(record, proposal)

    assert target.read_bytes() == desired
    assert result.recovery.original_bytes == original
    assert result.resulting_hash == content_hash(desired)


def test_deletes_file_and_preserves_original(tmp_path):
    target = tmp_path / "obsolete.py"
    original = b"OLD = True\n"
    target.write_bytes(original)

    writer = WorkspaceWriter(Workspace(tmp_path))
    proposal = FileChange(
        path="obsolete.py",
        change_type=ChangeType.DELETE,
        reason="Remove obsolete file",
    )
    record = make_change("obsolete.py", "delete", original, None)

    result = writer.apply(record, proposal)

    assert not target.exists()
    assert result.recovery.original_bytes == original


def test_rejects_unapproved_change(tmp_path):
    target = tmp_path / "example.py"
    target.write_text("OLD = 1\n", encoding="utf-8")
    original = target.read_bytes()
    desired = b"NEW = 1\n"

    writer = WorkspaceWriter(Workspace(tmp_path))
    proposal = FileChange(
        path="example.py",
        change_type=ChangeType.MODIFY,
        reason="Update",
        content=desired.decode(),
    )
    record = make_change(
        "example.py", "modify", original, desired, approved=False
    )

    with pytest.raises(WorkspaceWriteError, match="not approved"):
        writer.apply(record, proposal)

    assert target.read_bytes() == original


def test_rejects_stale_file(tmp_path):
    target = tmp_path / "example.py"
    target.write_bytes(b"ORIGINAL = 1\n")
    original = target.read_bytes()
    desired = b"NEW = 1\n"

    writer = WorkspaceWriter(Workspace(tmp_path))
    proposal = FileChange(
        path="example.py",
        change_type=ChangeType.MODIFY,
        reason="Update",
        content=desired.decode(),
    )
    record = make_change("example.py", "modify", original, desired)

    target.write_bytes(b"CHANGED ELSEWHERE = True\n")

    with pytest.raises(WorkspaceWriteError):
        writer.apply(record, proposal)


def test_create_never_overwrites_existing_file(tmp_path):
    target = tmp_path / "new.py"
    target.write_bytes(b"KEEP ME\n")
    desired = b"REPLACE ME\n"

    writer = WorkspaceWriter(Workspace(tmp_path))
    proposal = FileChange(
        path="new.py",
        change_type=ChangeType.CREATE,
        reason="Create",
        content=desired.decode(),
    )
    record = make_change("new.py", "create", None, desired)

    with pytest.raises(WorkspaceWriteError):
        writer.apply(record, proposal)

    assert target.read_bytes() == b"KEEP ME\n"



def test_rejects_parent_traversal_without_writing(tmp_path):
    outside = tmp_path.parent / "outside-alisa-test.txt"
    original = b"DO NOT CHANGE\n"
    outside.write_bytes(original)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(ValueError, match="traverse outside"):
        FileChange(
            path="../outside-alisa-test.txt",
            change_type=ChangeType.CREATE,
            reason="Unsafe write",
            content="CHANGED\n",
        )

    assert outside.read_bytes() == original



def test_recovery_bytes_match_original_for_modify(tmp_path):
    target = tmp_path / "example.py"
    original = b"before\n"
    desired = b"after\n"
    target.write_bytes(original)

    writer = WorkspaceWriter(Workspace(tmp_path))
    proposal = FileChange(
        path="example.py",
        change_type=ChangeType.MODIFY,
        reason="Change content",
        content=desired.decode(),
    )
    record = make_change("example.py", "modify", original, desired)

    result = writer.apply(record, proposal)

    assert result.recovery.original_bytes == original
    assert content_hash(result.recovery.original_bytes) == record.original_hash


def test_rollback_create_removes_created_file(tmp_path):
    workspace = Workspace(tmp_path)
    writer = WorkspaceWriter(workspace)
    target = tmp_path / "new_file.txt"
    target.write_bytes(b"created by ALISA")

    recovery = RecoveryRecord(
        change_id="rollback-create",
        relative_path="new_file.txt",
        operation="create",
        original_bytes=None,
        resulting_hash=content_hash(b"created by ALISA"),
    )

    result = writer.rollback(recovery)

    assert result.applied is True
    assert result.operation == "rollback_create"
    assert not target.exists()


def test_rollback_modify_restores_original_bytes(tmp_path):
    workspace = Workspace(tmp_path)
    writer = WorkspaceWriter(workspace)
    target = tmp_path / "existing.txt"

    original = b"original content\r\n"
    modified = b"modified content\n"
    target.write_bytes(modified)

    recovery = RecoveryRecord(
        change_id="rollback-modify",
        relative_path="existing.txt",
        operation="modify",
        original_bytes=original,
        resulting_hash=content_hash(modified),
    )

    result = writer.rollback(recovery)

    assert result.applied is True
    assert result.operation == "rollback_modify"
    assert target.read_bytes() == original


def test_rollback_delete_restores_deleted_file(tmp_path):
    workspace = Workspace(tmp_path)
    writer = WorkspaceWriter(workspace)
    target = tmp_path / "deleted.txt"

    original = b"file before deletion\n"
    recovery = RecoveryRecord(
        change_id="rollback-delete",
        relative_path="deleted.txt",
        operation="delete",
        original_bytes=original,
        resulting_hash=None,
    )

    result = writer.rollback(recovery)

    assert result.applied is True
    assert result.operation == "rollback_delete"
    assert target.read_bytes() == original


def test_rollback_refuses_to_overwrite_external_modification(tmp_path):
    workspace = Workspace(tmp_path)
    writer = WorkspaceWriter(workspace)
    target = tmp_path / "shared.txt"

    external_change = b"someone else's changes\n"
    target.write_bytes(external_change)

    recovery = RecoveryRecord(
        change_id="rollback-stale",
        relative_path="shared.txt",
        operation="modify",
        original_bytes=b"original content\n",
        resulting_hash=content_hash(b"ALISA's modification\n"),
    )

    with pytest.raises(
        WorkspaceWriteError,
        match="changed after the operation",
    ):
        writer.rollback(recovery)

    assert target.read_bytes() == external_change


def test_apply_create_then_rollback(tmp_path):
    workspace = Workspace(tmp_path)
    writer = WorkspaceWriter(workspace)
    desired = b"VALUE = 1\n"
    target = tmp_path / "new.py"

    proposal = FileChange(
        path="new.py",
        change_type=ChangeType.CREATE,
        reason="Create module",
        content=desired.decode(),
    )
    record = make_change("new.py", "create", None, desired)

    applied = writer.apply(record, proposal)
    assert applied.applied
    assert target.read_bytes() == desired

    rolled_back = writer.rollback(applied.recovery)

    assert rolled_back.applied
    assert rolled_back.operation == "rollback_create"
    assert not target.exists()


def test_apply_modify_then_rollback(tmp_path):
    workspace = Workspace(tmp_path)
    writer = WorkspaceWriter(workspace)
    target = tmp_path / "example.py"

    original = b"VALUE = 1\r\n"
    desired = b"VALUE = 2\n"
    target.write_bytes(original)

    proposal = FileChange(
        path="example.py",
        change_type=ChangeType.MODIFY,
        reason="Update value",
        content=desired.decode(),
    )
    record = make_change("example.py", "modify", original, desired)

    applied = writer.apply(record, proposal)
    assert target.read_bytes() == desired

    rolled_back = writer.rollback(applied.recovery)

    assert rolled_back.applied
    assert rolled_back.operation == "rollback_modify"
    assert target.read_bytes() == original


def test_apply_delete_then_rollback(tmp_path):
    workspace = Workspace(tmp_path)
    writer = WorkspaceWriter(workspace)
    target = tmp_path / "obsolete.py"

    original = b"OLD = True\r\n"
    target.write_bytes(original)

    proposal = FileChange(
        path="obsolete.py",
        change_type=ChangeType.DELETE,
        reason="Remove obsolete module",
    )
    record = make_change("obsolete.py", "delete", original, None)

    applied = writer.apply(record, proposal)
    assert not target.exists()

    rolled_back = writer.rollback(applied.recovery)

    assert rolled_back.applied
    assert rolled_back.operation == "rollback_delete"
    assert target.read_bytes() == original

