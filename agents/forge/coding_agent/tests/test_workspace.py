import json

import pytest

from coding_agent.core.models import FileChange
from coding_agent.core.workspace import ChangeSet, Workspace, WorkspaceError


@pytest.mark.parametrize("bad", [
    "../outside.py", "a/../../outside.py", "/etc/passwd", "C:/Windows/x.py", ".git/config",
    ".coding-agent/runs/x", ".env", "config/.env.local", "certs/server.key", "",
])
def test_guard_rejects_unsafe_paths(tmp_path, bad):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path).normalize(bad)


def test_guard_normalizes_windows_separators(tmp_path):
    assert Workspace(tmp_path).normalize(r"src\pkg\mod.py") == "src/pkg/mod.py"
    assert Workspace(tmp_path).normalize("./src/./a.py") == "src/a.py"


def test_changeset_overlays_and_normalizes_actions(tmp_path):
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    changes = ChangeSet(Workspace(tmp_path))
    assert changes.add(FileChange("new.py", "modify", "y = 2\n")).action == "create"
    assert changes.add(FileChange("old.py", "create", "x = 2\n")).action == "modify"
    assert changes.current("old.py") == "x = 2\n"
    assert changes.current("untouched.py") is None
    assert (tmp_path / "old.py").read_text(encoding="utf-8") == "x = 1\n"


def test_apply_writes_backs_up_and_journals(tmp_path):
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    workspace = Workspace(tmp_path)
    run_dir = tmp_path / ".coding-agent" / "runs" / "r1"
    written = workspace.apply([
        FileChange("old.py", "modify", "x = 2\n", "python_developer", "W1"),
        FileChange("pkg/new.py", "create", "y = 1\n", "python_developer", "W1"),
    ], run_dir)
    assert written == ["old.py", "pkg/new.py"]
    assert (tmp_path / "old.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (run_dir / "backup" / "old.py").read_text(encoding="utf-8") == "x = 1\n"
    lines = [json.loads(line) for line in (run_dir / "changes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["path"] for e in lines] == ["old.py", "pkg/new.py"] and "content" not in lines[0]


def test_list_files_skips_ignored_and_secret_files(tmp_path):
    for rel in ("a.py", "node_modules/x.js", ".venv/y.py", ".env", "src/b.py", ".coding-agent/memory/M.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert Workspace(tmp_path).list_files() == ["a.py", "src/b.py"]


def test_list_files_skips_environments_whatever_their_name(tmp_path):
    for rel in ("snake-game/main.py", "snake-game-venv/pyvenv.cfg",
                "snake-game-venv/Lib/site-packages/pip/_vendor/distlib/compat.py",
                "tools/conda-env/conda-meta/history", "tools/conda-env/lib/x.py",
                "vendor/Lib/site-packages/pygame/__init__.py", "vendor/helper.py"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert Workspace(tmp_path).list_files() == ["snake-game/main.py", "vendor/helper.py"]
