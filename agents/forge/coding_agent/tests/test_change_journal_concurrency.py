
import subprocess
import sys

from coding_agent.change_journal import ChangeJournal


def test_concurrent_processes_append_unique_sequential_entries(tmp_path):
    journal_path = tmp_path / "journal.jsonl"

    script = """
import sys
from coding_agent.change_journal import ChangeJournal

path, worker = sys.argv[1], sys.argv[2]
journal = ChangeJournal(path)

for index in range(15):
    journal.append(
        change_id=f"{worker}-{index}",
        event="apply_intent",
        relative_path="example.txt",
        operation="modify",
        state="approved",
        original_hash="original",
        resulting_hash="updated",
    )
"""

    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(journal_path), f"worker-{i}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(2)
    ]

    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (
            f"Subprocess failed:\n{stdout}\n{stderr}"
        )

    entries = ChangeJournal(journal_path).read_all()

    assert len(entries) == 30
    assert [entry.sequence for entry in entries] == list(range(1, 31))
    assert len({entry.change_id for entry in entries}) == 30
