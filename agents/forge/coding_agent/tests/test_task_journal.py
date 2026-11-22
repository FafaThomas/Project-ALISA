import json

from coding_agent.task_journal import TaskEventJournal


def test_task_event_journal_appends_attempt_events(tmp_path):
    path = tmp_path / ".coding-agent" / "tasks.jsonl"
    journal = TaskEventJournal(path)

    journal.append(
        task_id="task-1",
        event="attempt_failed",
        attempt=1,
        details={"phase": "verification", "errors": ["assertion failed"]},
    )
    journal.append(
        task_id="task-1",
        event="retry_approved",
        attempt=1,
        details={"phase": "verification"},
    )

    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [entry["event"] for entry in entries] == ["attempt_failed", "retry_approved"]
    assert entries[0]["details"]["errors"] == ["assertion failed"]
    assert all(entry["task_id"] == "task-1" for entry in entries)
