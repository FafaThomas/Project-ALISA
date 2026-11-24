"""The Debugging Specialist's test run: hangs and crashes go back for repair; the workspace is never touched."""

import pytest
from conftest import ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core import testrun
from coding_agent.core.models import FileChange
from coding_agent.core.workspace import ChangeSet, Workspace

UI = InvocationContext(source=InvocationSource.UI)

DEADLOCK = '''import threading, sys
lock = threading.Lock()

def save():
    with lock:
        pass

def add():
    with lock:
        save()          # acquires the same lock again: hangs

if __name__ == "__main__":
    add()
    print("added")
'''
FIXED = '''import threading, sys
lock = threading.Lock()

def save():
    pass

def add():
    with lock:
        save()

if __name__ == "__main__":
    add()
    print("added")
'''
CRASH = 'if __name__ == "__main__":\n    print("x".isoformat())\n'
CLEAN_ERROR = 'import sys\nif __name__ == "__main__":\n    print("Error: ID 99 not found", file=sys.stderr)\n    sys.exit(1)\n'


@pytest.fixture(autouse=True)
def test_run_on(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "repair")
    monkeypatch.setenv("CODING_AGENT_TEST_TIMEOUT", "3")


def debugger(commands, repaired=None):
    def handle(prompt, schema):
        if "commands" in (schema or {}).get("properties", {}):
            return {"summary": "plan", "commands": [{"command": c, "purpose": "try it"} for c in commands]}
        files = [{"path": "app.py", "action": "modify", "content": repaired}] if repaired else []
        return {"summary": "repair", "files": files}
    return handle


def run_app(tmp_path, first_version, commands, repaired=None, **request):
    handlers = {"python_developer": {"summary": "wrote", "files": [
        {"path": "app.py", "action": "create", "content": first_version}]},
        "debugging_specialist": debugger(commands, repaired)}
    llm = ScriptedLLM(handlers)
    report = CodingAgent.with_client(llm).unit("python_developer").run(
        CodingRequest(task="Write app.py", workspace=str(tmp_path / "ws"), **request), UI)
    return report, llm


def test_a_hang_is_caught_and_sent_for_repair(tmp_path):
    report, llm = run_app(tmp_path, DEADLOCK, ["python app.py"], repaired=FIXED)
    assert report.status == "completed", report.error
    repair = next(c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist"
                  and "commands" not in (c["schema"] or {}).get("properties", {}))
    assert "[test-run]" in repair and "hung: no exit within 3s" in repair
    assert (tmp_path / "ws" / "app.py").read_text(encoding="utf-8") == FIXED
    assert report.test_run[0]["exit_code"] == 0 and "added" in report.test_run[0]["stdout"]
    assert not report.warnings


def test_a_crash_is_reported_with_its_traceback(tmp_path):
    report, llm = run_app(tmp_path, CRASH, ["python app.py"])
    repair = [c["prompt"] for c in llm.calls if "commands" not in (c["schema"] or {}).get("properties", {})
              and c["role"] == "debugging_specialist"][0]
    assert "crashed with a traceback" in repair and "AttributeError" in repair and "app.py" in repair


def test_leftover_failures_warn_by_default_and_block_on_request(tmp_path, monkeypatch):
    report, _ = run_app(tmp_path / "a", CRASH, ["python app.py"])
    assert report.status == "completed"
    assert any(w.startswith("test run: `python app.py` crashed") for w in report.warnings)
    assert (tmp_path / "a" / "ws" / "app.py").exists()

    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "block")
    report, _ = run_app(tmp_path / "b", CRASH, ["python app.py"])
    assert report.status == "failed" and not (tmp_path / "b" / "ws" / "app.py").exists()


def test_a_clean_error_exit_is_not_a_failure(tmp_path):
    report, llm = run_app(tmp_path, CLEAN_ERROR, ["python app.py"])
    assert report.status == "completed" and not report.warnings
    assert report.test_run[0]["exit_code"] == 1
    assert sum(c["role"] == "debugging_specialist" for c in llm.calls) == 1  # planned, nothing to repair


def test_off_skips_the_test_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "off")
    report, llm = run_app(tmp_path, DEADLOCK, ["python app.py"])
    assert report.status == "completed" and report.test_run == []
    assert "debugging_specialist" not in llm.roles_called()


def test_only_project_python_commands_run(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    changes.add(FileChange("app.py", "create", 'print("hi")\n'))
    results = testrun.run_commands(changes, [
        {"command": "pip install requests"}, {"command": "python ../outside.py"},
        {"command": "python -c \"print(1)\""}, {"command": "rm -rf ."}, {"command": "python missing.py"},
        {"command": "python app.py delete <ID_OF_FIRST>"}, {"command": "python app.py"}], 5)
    assert [bool(r.skipped) for r in results] == [True, True, True, True, True, True, False]
    assert "placeholder" in results[5].skipped
    assert results[-1].stdout.strip() == "hi"


def test_sandbox_has_no_secrets_and_leaves_the_workspace_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret")
    monkeypatch.setenv("CODING_AGENT_API_TOKEN", "token")
    script = ('import os, pathlib\n'
              'print(os.environ.get("GEMINI_API_KEY"), os.environ.get("CODING_AGENT_API_TOKEN"))\n'
              'print(pathlib.Path.home().parent.name.startswith("coding-agent-testrun-"))\n'
              'pathlib.Path("data.json").write_text("{}")\n')
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".env").write_text("GEMINI_API_KEY=x\n", encoding="utf-8")
    changes = ChangeSet(Workspace(ws))
    changes.add(FileChange("app.py", "create", script))
    result = testrun.run_commands(changes, [{"command": "python app.py"}, {"command": "python app.py"}], 5)[0]
    assert result.stdout.split() == ["None", "None", "True"]
    assert not (ws / "data.json").exists() and not (ws / "app.py").exists()


def test_fallback_runs_help_on_entry_points(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    changes.add(FileChange("main.py", "create", 'if __name__ == "__main__":\n    pass\n'))
    changes.add(FileChange("lib.py", "create", "x = 1\n"))
    assert testrun.fallback_commands(changes) == [{"command": "python main.py --help",
                                                   "purpose": "starts without crashing"}]
