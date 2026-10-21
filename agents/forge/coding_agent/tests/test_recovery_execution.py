
from src.coding_agent.change_journal import ChangeJournal,JournalError
from src.coding_agent.change_tracking import content_hash
from src.coding_agent.recovery import (
    RecoveryAction,
    RecoveryCandidate,
    RecoveryDecision,
    RecoveryStatus,
    execute_recovery,
)
from src.coding_agent.workspace import Workspace



def test_restore_modified_file(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "example.txt"
    original = b"original contents"
    updated = b"updated contents"
    target.write_bytes(updated)

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    journal.append(
        change_id="restore-modify",
        event="rollback_intent",
        relative_path="example.txt",
        operation="modify",
        state="applied",
        original_hash=content_hash(original),
        resulting_hash=content_hash(updated),
        recovery_bytes=original,
    )

    candidate = RecoveryCandidate(
        change_id="restore-modify",
        relative_path="example.txt",
        operation="modify",
        last_event="rollback_intent",
        state="applied",
        original_hash=content_hash(original),
        resulting_hash=content_hash(updated),
        recovery_bytes=original,
        assessment="test",
        status=RecoveryStatus.APPLIED,
        current_hash=content_hash(updated),
    )
    decision = RecoveryDecision(
        candidate.change_id,
        RecoveryAction.RESTORE_ORIGINAL,
        "test",
    )

    result = execute_recovery(candidate, decision, journal, Workspace(root))

    assert result.executed
    assert result.journaled
    assert target.read_bytes() == original
    assert journal.read_all()[-1].event == "rolled_back"


def test_refuses_if_file_changed_after_assessment(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "example.txt"
    target.write_bytes(b"unexpected contents")

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    candidate = RecoveryCandidate(
        change_id="changed-after-assessment",
        relative_path="example.txt",
        operation="modify",
        last_event="rollback_intent",
        state="applied",
        original_hash=content_hash(b"original contents"),
        resulting_hash=content_hash(b"updated contents"),
        recovery_bytes=b"original contents",
        assessment="test",
        status=RecoveryStatus.APPLIED,
        current_hash=content_hash(b"updated contents"),
    )
    decision = RecoveryDecision(
        candidate.change_id,
        RecoveryAction.RESTORE_ORIGINAL,
        "test",
    )

    result = execute_recovery(candidate, decision, journal, Workspace(root))

    assert not result.executed
    assert target.read_bytes() == b"unexpected contents"


def test_restore_deleted_file(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    original = b"original deleted-file contents"
    original_hash = content_hash(original)

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    journal.append(
        change_id="restore-delete",
        event="rollback_intent",
        relative_path="deleted.txt",
        operation="delete",
        state="applied",
        original_hash=original_hash,
        resulting_hash=None,
        recovery_bytes=original,
    )

    candidate = RecoveryCandidate(
        change_id="restore-delete",
        relative_path="deleted.txt",
        operation="delete",
        last_event="rollback_intent",
        state="applied",
        original_hash=original_hash,
        resulting_hash=None,
        recovery_bytes=original,
        assessment="test",
        status=RecoveryStatus.APPLIED,
        current_hash=None,
    )
    decision = RecoveryDecision(
        candidate.change_id,
        RecoveryAction.RESTORE_ORIGINAL,
        "test",
    )

    result = execute_recovery(candidate, decision, journal, Workspace(root))

    assert result.executed
    assert result.journaled
    assert (root / "deleted.txt").read_bytes() == original
    assert journal.read_all()[-1].event == "rolled_back"


def test_remove_created_file_during_recovery(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "created.txt"
    updated = b"contents from interrupted create"
    target.write_bytes(updated)

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    journal.append(
        change_id="restore-create",
        event="rollback_intent",
        relative_path="created.txt",
        operation="create",
        state="applied",
        original_hash=None,
        resulting_hash=content_hash(updated),
    )

    candidate = RecoveryCandidate(
        change_id="restore-create",
        relative_path="created.txt",
        operation="create",
        last_event="rollback_intent",
        state="applied",
        original_hash=None,
        resulting_hash=content_hash(updated),
        recovery_bytes=None,
        assessment="test",
        status=RecoveryStatus.APPLIED,
        current_hash=content_hash(updated),
    )
    decision = RecoveryDecision(
        candidate.change_id,
        RecoveryAction.RESTORE_ORIGINAL,
        "test",
    )

    result = execute_recovery(candidate, decision, journal, Workspace(root))

    assert result.executed
    assert result.journaled
    assert not target.exists()
    assert journal.read_all()[-1].event == "rolled_back"


def test_restore_reports_journal_failure_after_file_recovery(
    tmp_path, monkeypatch
):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "example.txt"
    original = b"original contents"
    updated = b"updated contents"
    target.write_bytes(updated)

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    journal.append(
        change_id="restore-journal-failure",
        event="rollback_intent",
        relative_path="example.txt",
        operation="modify",
        state="applied",
        original_hash=content_hash(original),
        resulting_hash=content_hash(updated),
        recovery_bytes=original,
    )

    candidate = RecoveryCandidate(
        change_id="restore-journal-failure",
        relative_path="example.txt",
        operation="modify",
        last_event="rollback_intent",
        state="applied",
        original_hash=content_hash(original),
        resulting_hash=content_hash(updated),
        recovery_bytes=original,
        assessment="test",
        status=RecoveryStatus.APPLIED,
        current_hash=content_hash(updated),
    )
    decision = RecoveryDecision(
        candidate.change_id,
        RecoveryAction.RESTORE_ORIGINAL,
        "test",
    )

    def fail_append(**kwargs):
        raise JournalError("Simulated journal disk failure")

    monkeypatch.setattr(journal, "append", fail_append)

    result = execute_recovery(candidate, decision, journal, Workspace(root))

    assert result.executed
    assert not result.journaled
    assert "journaling failed" in result.message.lower()
    assert target.read_bytes() == original

