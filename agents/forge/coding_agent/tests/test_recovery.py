from src.coding_agent.change_journal import ChangeJournal
from src.coding_agent.change_tracking import content_hash
from src.coding_agent.recovery import RecoveryStatus, assess_recovery
from src.coding_agent.workspace import Workspace


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
