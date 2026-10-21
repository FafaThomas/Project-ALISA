from coding_agent.change_journal import ChangeJournal
from coding_agent.change_tracking import ChangeState, WorkspaceChange, content_hash
from coding_agent.coordinator import ChangeCoordinator
from coding_agent.models import ChangeType, FileChange
from coding_agent.workspace import Workspace
from coding_agent.change_journal import JournalError


def make_change(path, operation, original, desired):
    return WorkspaceChange(
        change_id=f"journal-{operation}",
        relative_path=path,
        operation=operation,
        state=ChangeState.APPROVED,
        original_hash=content_hash(original),
        resulting_hash=content_hash(desired),
        reason="Journal integration test",
    )


def test_apply_verify_rollback_are_journaled(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = Workspace(workspace_root)
    journal = ChangeJournal(tmp_path / "journal.jsonl")
    coordinator = ChangeCoordinator(workspace, journal=journal)

    original = b"VALUE = 1\n"
    desired = b"VALUE = 2\n"
    target = workspace.root / "example.py"
    target.write_bytes(original)

    change = make_change("example.py", "modify", original, desired)
    proposal = FileChange(
        path="example.py",
        change_type=ChangeType.MODIFY,
        reason="Update value",
        content=desired.decode(),
    )

    applied = coordinator.apply(change, proposal)
    assert applied.change.state == ChangeState.APPLIED

    verified = coordinator.verify(applied)
    assert verified.change.state == ChangeState.VERIFIED

    rolled_back = coordinator.rollback(verified)
    assert rolled_back.change.state == ChangeState.ROLLED_BACK
    assert target.read_bytes() == original

    events = [entry.event for entry in journal.read_all()]
    assert events == [
        "apply_intent",
        "applied",
        "verified",
        "rollback_intent",
        "rolled_back",
    ]


def test_modify_intent_persists_original_bytes_before_write(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = Workspace(workspace_root)
    journal = ChangeJournal(tmp_path / "journal.jsonl")
    coordinator = ChangeCoordinator(workspace, journal=journal)

    original = b"ORIGINAL\r\n"
    desired = b"UPDATED\n"
    target = workspace.root / "example.txt"
    target.write_bytes(original)

    change = make_change("example.txt", "modify", original, desired)
    proposal = FileChange(
        path="example.txt",
        change_type=ChangeType.MODIFY,
        reason="Update file",
        content=desired.decode(),
    )

    result = coordinator.apply(change, proposal)

    assert result.change.state == ChangeState.APPLIED
    first_entry = journal.read_all()[0]
    assert first_entry.event == "apply_intent"
    assert first_entry.recovery_bytes() == original
    assert target.read_bytes() == desired


def test_intent_journal_failure_prevents_file_modification(
    tmp_path, monkeypatch
):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = Workspace(workspace_root)

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    coordinator = ChangeCoordinator(workspace, journal=journal)

    original = b"ORIGINAL\n"
    desired = b"UPDATED\n"
    target = workspace_root / "example.txt"
    target.write_bytes(original)

    change = make_change("example.txt", "modify", original, desired)
    proposal = FileChange(
        path="example.txt",
        change_type=ChangeType.MODIFY,
        reason="Test intent failure",
        content=desired.decode(),
    )

    def fail_append(**kwargs):
        raise JournalError("Simulated disk failure")

    monkeypatch.setattr(journal, "append", fail_append)

    result = coordinator.apply(change, proposal)

    assert result.change.state == ChangeState.FAILED
    assert "Journal rejected write intent" in result.change.error
    assert target.read_bytes() == original


def test_applied_journal_failure_attempts_rollback(
    tmp_path, monkeypatch
):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = Workspace(workspace_root)

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    coordinator = ChangeCoordinator(workspace, journal=journal)

    original = b"ORIGINAL\n"
    desired = b"UPDATED\n"
    target = workspace_root / "example.txt"
    target.write_bytes(original)

    change = make_change("example.txt", "modify", original, desired)
    proposal = FileChange(
        path="example.txt",
        change_type=ChangeType.MODIFY,
        reason="Test outcome failure",
        content=desired.decode(),
    )

    original_append = journal.append

    def fail_applied_event(**kwargs):
        if kwargs["event"] == "applied":
            raise JournalError("Simulated outcome write failure")
        return original_append(**kwargs)

    monkeypatch.setattr(journal, "append", fail_applied_event)

    result = coordinator.apply(change, proposal)

    assert result.change.state == ChangeState.ROLLED_BACK
    assert "rolled back" in result.change.error.lower()
    assert target.read_bytes() == original
