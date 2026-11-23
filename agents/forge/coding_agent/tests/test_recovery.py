from coding_agent.change_journal import ChangeJournal
from coding_agent.change_tracking import content_hash
from coding_agent.recovery import RecoveryStatus, assess_recovery
from coding_agent.workspace import Workspace


def test_assessment_finds_interrupted_apply(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    original = b"original contents"
    resulting = b"updated contents"
    (workspace_root / "example.txt").write_bytes(original)

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    journal.append(
        change_id="change-1",
        event="apply_intent",
        relative_path="example.txt",
        operation="modify",
        state="approved",
        original_hash=content_hash(original),
        resulting_hash=content_hash(resulting),
        recovery_bytes=original,
    )

    candidates = assess_recovery(journal, Workspace(workspace_root))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.change_id == "change-1"
    assert candidate.last_event == "apply_intent"
    assert candidate.recovery_bytes == original
    assert candidate.status == RecoveryStatus.ORIGINAL
    assert "original state" in candidate.assessment


def test_assessment_ignores_completed_changes(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    journal = ChangeJournal(tmp_path / "journal.jsonl")

    journal.append(
        change_id="change-2",
        event="apply_intent",
        relative_path="example.txt",
        operation="modify",
        state="approved",
        original_hash=content_hash(b"original contents"),
        resulting_hash=content_hash(b"updated contents"),
        recovery_bytes=b"original contents",
    )
    journal.append(
        change_id="change-2",
        event="verified",
        relative_path="example.txt",
        operation="modify",
        state="verified",
        original_hash=content_hash(b"original contents"),
        resulting_hash=content_hash(b"updated contents"),
    )

    assert assess_recovery(journal, Workspace(workspace_root)) == []


def test_assessment_uses_latest_event_per_change(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    original = b"original contents"
    resulting = b"updated contents"
    (workspace_root / "example.txt").write_bytes(resulting)

    journal = ChangeJournal(tmp_path / "journal.jsonl")
    for event, state in [("apply_intent", "approved"), ("applied", "applied")]:
        journal.append(
            change_id="change-3",
            event=event,
            relative_path="example.txt",
            operation="modify",
            state=state,
            original_hash=content_hash(original),
            resulting_hash=content_hash(resulting),
            recovery_bytes=original,
        )

    candidates = assess_recovery(journal, Workspace(workspace_root))

    assert len(candidates) == 1
    assert candidates[0].last_event == "applied"
    assert candidates[0].status == RecoveryStatus.APPLIED
    assert "expected result" in candidates[0].assessment


def test_assessment_flags_unexpected_workspace_contents(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    unexpected = b"unexpected contents"
    target = workspace_root / "example.txt"
    target.write_bytes(unexpected)

    original = b"original contents"
    resulting = b"updated contents"
    journal = ChangeJournal(tmp_path / "journal.jsonl")
    journal.append(
        change_id="change-conflict",
        event="apply_intent",
        relative_path="example.txt",
        operation="modify",
        state="approved",
        original_hash=content_hash(original),
        resulting_hash=content_hash(resulting),
        recovery_bytes=original,
    )

    candidates = assess_recovery(journal, Workspace(workspace_root))

    assert len(candidates) == 1
    assert candidates[0].status == RecoveryStatus.CONFLICT
    assert "manual investigation" in candidates[0].assessment
    assert target.read_bytes() == unexpected


def test_assessment_recognizes_interrupted_directory_creation(tmp_path):
    from coding_agent.change_tracking import DIRECTORY_STATE_HASH

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "frontend").mkdir()
    journal = ChangeJournal(tmp_path / "journal.jsonl")
    journal.append(
        change_id="mkdir-recovery",
        event="apply_intent",
        relative_path="frontend",
        operation="mkdir",
        state="approved",
        original_hash=None,
        resulting_hash=DIRECTORY_STATE_HASH,
    )

    candidate = assess_recovery(journal, Workspace(workspace_root))[0]
    assert candidate.status == RecoveryStatus.APPLIED
    assert candidate.operation == "mkdir"


def test_recovery_can_finish_interrupted_empty_directory_rollback(tmp_path):
    from coding_agent.change_tracking import DIRECTORY_STATE_HASH
    from coding_agent.recovery import RecoveryAction, decide_recovery, execute_recovery

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "frontend").mkdir()
    journal = ChangeJournal(tmp_path / "journal.jsonl")
    for event, state in (("apply_intent", "approved"), ("applied", "applied"), ("rollback_intent", "applied")):
        journal.append(
            change_id="mkdir-rollback-recovery",
            event=event,
            relative_path="frontend",
            operation="mkdir",
            state=state,
            original_hash=None,
            resulting_hash=DIRECTORY_STATE_HASH,
        )

    workspace = Workspace(workspace_root)
    candidate = assess_recovery(journal, workspace)[0]
    decision = decide_recovery(candidate)
    assert decision.action == RecoveryAction.RESTORE_ORIGINAL

    result = execute_recovery(candidate, decision, journal, workspace)
    assert result.executed and result.journaled
    assert not (workspace_root / "frontend").exists()
