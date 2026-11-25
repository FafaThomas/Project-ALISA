"""File ownership: a specialist may not overwrite files assigned to another work item."""

import pytest
from conftest import ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core.models import FileChange
from coding_agent.core.workspace import ChangeSet, Workspace, WorkspaceError

UI = InvocationContext(source=InvocationSource.UI)
STORAGE = "def load():\n    return []\n"
MODELS_GOOD = "class Expense:\n    @classmethod\n    def from_dict(cls, d):\n        return cls()\n"
MODELS_FROM_DEBUGGER = "class Expense:\n    pass\n"
MAIN = "from models import Expense\n"


def test_changeset_rejects_writes_to_files_owned_by_another_item(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    changes.claim("models.py", "W2", "python_developer")
    changes.add(FileChange("models.py", "create", MODELS_GOOD, "python_developer", "W2"))
    with pytest.raises(WorkspaceError, match="belongs to work item W2 \\(python_developer\\)"):
        changes.add(FileChange("models.py", "modify", "x = 1\n", "debugging_specialist", "W1"))
    assert changes.current("models.py") == MODELS_GOOD


def test_unclaimed_files_repairs_and_sub_items_are_allowed(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    changes.claim("storage.py", "W1", "database_developer")
    changes.add(FileChange("helpers.py", "create", "x = 1\n", "python_developer", "W9"))     # nobody owns it
    changes.add(FileChange("storage.py", "create", "a = 1\n", "postgres_developer", "W1.a"))  # team sub-item
    changes.add(FileChange("storage.py", "modify", "a = 2\n", "debugging_specialist", "repair"))  # repair round
    assert changes.current("storage.py") == "a = 2\n"


def test_the_expense_tracker_overwrite_cannot_happen_again(tmp_path):
    handlers = {
        "engineering_director": {"rationale": "bug fix", "team": ["python_developer", "debugging_specialist"]},
        "tech_lead": {"summary": "plan", "work_items": [
            {"id": "W1", "title": "deadlock", "owner_role": "debugging_specialist", "description": "",
             "files": ["storage.py"]},
            {"id": "W2", "title": "model", "owner_role": "python_developer", "description": "",
             "files": ["models.py"]},
            {"id": "W3", "title": "cli", "owner_role": "python_developer", "description": "",
             "files": ["main.py"]},
        ]},
        # Owns storage.py but also rewrites the developers' files, as in the real run.
        "debugging_specialist": {"summary": "fixed everything", "files": [
            {"path": "storage.py", "action": "create", "content": STORAGE},
            {"path": "models.py", "action": "create", "content": MODELS_FROM_DEBUGGER},
            {"path": "main.py", "action": "create", "content": "print('debugger main')\n"},
        ]},
        "python_developer": lambda p, s: (
            {"summary": "model", "files": [{"path": "models.py", "action": "create", "content": MODELS_GOOD}]}
            if "Your work item W2" in p else
            {"summary": "cli", "files": [{"path": "main.py", "action": "create", "content": MAIN}]}),
    }
    llm = ScriptedLLM(handlers)
    report = CodingAgent.with_client(llm).run(
        CodingRequest(task="Fix the bugs in the expense tracker", workspace=str(tmp_path)), UI)

    assert report.status == "completed", report.error
    assert (tmp_path / "models.py").read_text(encoding="utf-8") == MODELS_GOOD
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == MAIN
    authors = {c.path: c.author_role for c in report.changes}
    assert authors == {"storage.py": "debugging_specialist", "models.py": "python_developer",
                       "main.py": "python_developer"}
    rejected = [w for w in report.warnings if "owned by other work items" in w]
    assert len(rejected) == 1 and "debugging_specialist" in rejected[0] and "rejected 2 file(s)" in rejected[0]
    assert "Write only these" in next(c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist")
