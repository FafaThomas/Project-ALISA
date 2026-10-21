import base64
import json

import pytest

from coding_agent.change_journal import ChangeJournal, JournalError


def test_append_and_read_entry(tmp_path):
    journal = ChangeJournal(tmp_path / "journal.jsonl")

    written = journal.append(
        change_id="change-001",
        event="approved",
        relative_path="src/example.py",
        operation="modify",
        state="approved",
        original_hash="old-hash",
        resulting_hash="new-hash",
    )

    entries = journal.read_all()

    assert len(entries) == 1
    assert entries[0] == written
    assert entries[0].sequence == 1
    assert entries[0].event == "approved"


def test_sequence_increments_across_appends(tmp_path):
    journal = ChangeJournal(tmp_path / "journal.jsonl")

    first = journal.append(
        change_id="change-001",
        event="approved",
        relative_path="example.py",
        operation="modify",
        state="approved",
    )
    second = journal.append(
        change_id="change-001",
        event="applied",
        relative_path="example.py",
        operation="modify",
        state="applied",
    )

    assert first.sequence == 1
    assert second.sequence == 2


def test_recovery_bytes_round_trip(tmp_path):
    journal = ChangeJournal(tmp_path / "journal.jsonl")
    original = b"original bytes\r\n\x00"

    journal.append(
        change_id="change-002",
        event="applied",
        relative_path="example.bin",
        operation="modify",
        state="applied",
        recovery_bytes=original,
    )

    entry = journal.read_all()[0]
    assert entry.recovery_bytes() == original


def test_missing_recovery_payload_returns_none(tmp_path):
    journal = ChangeJournal(tmp_path / "journal.jsonl")

    journal.append(
        change_id="change-003",
        event="approved",
        relative_path="example.py",
        operation="create",
        state="approved",
    )

    assert journal.read_all()[0].recovery_bytes() is None


def test_rejects_empty_change_id(tmp_path):
    journal = ChangeJournal(tmp_path / "journal.jsonl")

    with pytest.raises(JournalError, match="change_id"):
        journal.append(
            change_id=" ",
            event="approved",
            relative_path="example.py",
            operation="create",
            state="approved",
        )


def test_rejects_malformed_journal_record(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text("{not valid json}\n", encoding="utf-8")
    journal = ChangeJournal(path)

    with pytest.raises(JournalError, match="line 1"):
        journal.read_all()


def test_rejects_invalid_sequence(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text(
        json.dumps({
            "sequence": 5,
            "timestamp": "2026-10-21T00:00:00+00:00",
            "change_id": "change-004",
            "event": "approved",
            "relative_path": "example.py",
            "operation": "create",
            "state": "approved",
        }) + "\n",
        encoding="utf-8",
    )
    journal = ChangeJournal(path)

    with pytest.raises(JournalError, match="sequence"):
        journal.read_all()