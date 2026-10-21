
import json

import pytest

from coding_agent.change_journal import ChangeJournal, JournalError


def append_entry(journal, change_id):
    return journal.append(
        change_id=change_id,
        event="apply_intent",
        relative_path="example.txt",
        operation="modify",
        state="approved",
        original_hash="original",
        resulting_hash="updated",
        recovery_bytes=b"original bytes",
    )


def test_read_ignores_incomplete_final_record_without_modifying_file(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = ChangeJournal(path)
    append_entry(journal, "first")

    with path.open("ab") as handle:
        handle.write(b'{"sequence":2,"change_id":')

    before = path.read_bytes()
    entries = journal.read_all()

    assert len(entries) == 1
    assert entries[0].change_id == "first"
    assert path.read_bytes() == before


def test_next_append_repairs_incomplete_tail_and_preserves_sequence(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = ChangeJournal(path)
    append_entry(journal, "first")

    with path.open("ab") as handle:
        handle.write(b'{"sequence":2,"change_id":')

    second = append_entry(journal, "second")
    entries = journal.read_all()

    assert second.sequence == 2
    assert [entry.change_id for entry in entries] == ["first", "second"]


def test_malformed_terminated_record_is_not_silently_ignored(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = ChangeJournal(path)
    append_entry(journal, "first")

    with path.open("ab") as handle:
        handle.write(b"{broken}\n")

    with pytest.raises(JournalError, match="Invalid journal record"):
        journal.read_all()


def test_valid_json_without_final_newline_is_treated_as_incomplete(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = ChangeJournal(path)
    append_entry(journal, "first")

    complete_record = {
        "sequence": 2,
        "timestamp": "2026-10-21T00:00:00+00:00",
        "change_id": "second",
        "event": "applied",
        "relative_path": "example.txt",
        "operation": "modify",
        "state": "applied",
        "original_hash": "original",
        "resulting_hash": "updated",
        "recovery_bytes_b64": None,
        "error": None,
    }

    with path.open("ab") as handle:
        handle.write(json.dumps(complete_record).encode("utf-8"))

    assert [entry.change_id for entry in journal.read_all()] == ["first"]
