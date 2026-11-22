from __future__ import annotations

import json
from pathlib import Path

from coding_agent.cli import main


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "class UserService:\n    def get_user(self, user_id: int):\n        return user_id\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        "from app import UserService\n\nservice = UserService()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_index_command_reports_workspace_summary(tmp_path, capsys):
    workspace = _workspace(tmp_path)
    assert main(["index", str(workspace)]) == 0
    output = capsys.readouterr().out
    assert "Files: 2/2 indexed" in output
    assert "python" in output


def test_index_json_is_machine_readable(tmp_path, capsys):
    workspace = _workspace(tmp_path)
    assert main(["index", str(workspace), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["indexed_files"] == 2
    assert payload["symbols"] >= 1


def test_search_finds_symbol(tmp_path, capsys):
    workspace = _workspace(tmp_path)
    assert main(["search", str(workspace), "UserService"]) == 0
    assert "UserService" in capsys.readouterr().out


def test_graph_command_renders_graph(tmp_path, capsys):
    workspace = _workspace(tmp_path)
    assert main(["graph", str(workspace)]) == 0
    output = capsys.readouterr().out
    assert "KNOWLEDGE GRAPH" in output
    assert "Nodes:" in output


def test_relationships_command_requires_unique_target_or_exact_id(tmp_path, capsys):
    workspace = _workspace(tmp_path)
    assert main(["relationships", str(workspace), "symbol:app.py::UserService@1:class"]) == 0
    assert "RELATIONSHIPS:" in capsys.readouterr().out


def test_impact_command_handles_unknown_target(tmp_path, capsys):
    workspace = _workspace(tmp_path)
    assert main(["impact", str(workspace), "does-not-exist"]) == 2
    assert "No graph node matched" in capsys.readouterr().err


def test_context_command_assembles_task_context(tmp_path, capsys):
    workspace = _workspace(tmp_path)
    assert main(["context", str(workspace), "Find user service implementation"]) == 0
    output = capsys.readouterr().out
    assert "PROJECT CONTEXT" in output
    assert "app.py" in output


def test_rejects_invalid_depth(tmp_path, capsys):
    workspace = _workspace(tmp_path)
    assert main(["impact", str(workspace), "UserService", "--depth", "0"]) == 2
    assert "--depth must be at least 1" in capsys.readouterr().err


def _patch_coding_workflow(monkeypatch, *, command_succeeds=True):
    from coding_agent.models import (
        ChangeType,
        CommandResult,
        FileChange,
        ImplementationPlan,
    )

    class FakePlanner:
        def __init__(self, client=None):
            self.client = client

        def plan(self, task, context=""):
            return ImplementationPlan(
                task_id=task.task_id,
                summary="Update the application value",
                changes=(FileChange(
                    path="app.py",
                    change_type=ChangeType.MODIFY,
                    reason="Apply requested value",
                    content="VALUE = 2\n",
                ),),
                verification_commands=(("python", "-m", "pytest", "-q"),),
                requires_approval=True,
            )

    class FakeRunner:
        def __init__(self, workspace, *, timeout=120.0):
            self.workspace = workspace

        def run(self, command, *, approved_commands):
            return CommandResult(
                command=tuple(command),
                exit_code=0 if command_succeeds else 1,
                stdout="checks passed" if command_succeeds else "checks failed",
            )

    monkeypatch.setattr("coding_agent.cli.OllamaClient", lambda: object())
    monkeypatch.setattr("coding_agent.cli.CodingPlanner", FakePlanner)
    monkeypatch.setattr("coding_agent.cli.CommandRunner", FakeRunner)


def test_run_command_rejection_does_not_modify_workspace(tmp_path, capsys, monkeypatch):
    workspace = _workspace(tmp_path)
    original = (workspace / "app.py").read_text(encoding="utf-8")
    _patch_coding_workflow(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    result = main(["run", str(workspace), "Change the application value"])

    assert result == 2
    assert (workspace / "app.py").read_text(encoding="utf-8") == original
    output = capsys.readouterr().out
    assert "IMPLEMENTATION PLAN" in output
    assert "No changes applied" in output


def test_run_command_applies_approved_plan_and_verifies(tmp_path, capsys, monkeypatch):
    workspace = _workspace(tmp_path)
    _patch_coding_workflow(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")

    result = main(["run", str(workspace), "Change the application value"])

    assert result == 0
    assert (workspace / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    output = capsys.readouterr().out
    assert "TASK STATUS: COMPLETED" in output
    assert "Verification passed" in output
    assert (workspace / ".coding-agent" / "changes.jsonl").is_file()


def test_run_rejects_invalid_max_attempts(tmp_path, capsys):
    assert main([
        "run", str(tmp_path), "Do something", "--max-attempts", "0"
    ]) == 2
    assert "--max-attempts must be between 1 and 10" in capsys.readouterr().err


def test_collect_manual_verification_requires_explicit_decision_and_evidence(monkeypatch):
    from types import SimpleNamespace
    from coding_agent.cli import _collect_manual_verification

    answers = iter(("PASS: Inspected the output file and confirmed the expected configuration.", "FAIL: The readiness check is missing.", ""))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    criteria = (
        SimpleNamespace(criterion_id="VER-1", description="Review output file"),
        SimpleNamespace(criterion_id="VER-2", description="Review healthcheck"),
        SimpleNamespace(criterion_id="VER-3", description="Review report"),
    )
    evidence = _collect_manual_verification(criteria)
    assert evidence == {
        "VER-1": {"status": "PASS", "evidence": "Inspected the output file and confirmed the expected configuration."},
        "VER-2": {"status": "FAIL", "evidence": "The readiness check is missing."},
    }
