"""Fixes from the first real build test: files that fit together, no silent failures,
target platform, and create/modify labels."""

from conftest import CALC, ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core.pyinterface import import_problems, signatures
from coding_agent.core.units import default_target_environment

UI = InvocationContext(source=InvocationSource.UI)
CLI = "import argparse\n\n\ndef run_cli(argv=None) -> int:\n    return 0\n"
MAIN_BAD = "from cli import main_cli\n\nif __name__ == '__main__':\n    main_cli()\n"
MAIN_OK = "from cli import run_cli\n\nif __name__ == '__main__':\n    run_cli()\n"


def two_file_handlers(main_content):
    return {
        "engineering_director": {"rationale": "x", "team": ["python_developer"]},
        "tech_lead": {"summary": "plan", "work_items": [
            {"id": "W1", "title": "cli", "owner_role": "python_developer", "description": "run_cli(argv)",
             "files": ["cli.py"]},
            {"id": "W2", "title": "entry", "owner_role": "python_developer", "description": "calls run_cli",
             "files": ["main.py"]}]},
        "python_developer": lambda p, s: (
            {"summary": "cli", "files": [{"path": "cli.py", "action": "create", "content": CLI}]}
            if "Your work item W1" in p else
            {"summary": "main", "files": [{"path": "main.py", "action": "create", "content": main_content}]}),
        "debugging_specialist": {"summary": "fixed import", "files": [
            {"path": "main.py", "action": "modify", "content": MAIN_OK}]},
    }


def run_cli_task(llm, tmp_path):
    return CodingAgent.with_client(llm).run(CodingRequest(task="Build a CLI in Python", workspace=str(tmp_path)), UI)


# ---------------------------------------------------------------- fix 1
def test_import_check_finds_mismatched_names():
    files = {"main.py": MAIN_BAD, "cli.py": CLI, "util.py": "import json\nfrom os import path\n"}
    problems = import_problems(list(files), files.get)
    assert len(problems) == 1 and "imports 'main_cli' from cli.py" in problems[0]["detail"]
    assert "run_cli" in problems[0]["detail"]


def test_import_check_handles_packages_and_relative_imports():
    files = {
        "pkg/__init__.py": "from .core import helper\n",
        "pkg/core.py": "def helper():\n    pass\n",
        "pkg/use.py": "from . import core\nfrom .core import helper\nfrom pkg import core as c\n",
        "app.py": "from pkg.core import helper, nope\n",
    }
    problems = import_problems(list(files), files.get)
    assert [p["path"] for p in problems] == ["app.py"] and "'nope'" in problems[0]["detail"]


def test_signatures_summarize_a_module():
    source = ("class Store:\n    def __init__(self, path):\n        pass\n    def add(self, text: str) -> int:\n"
              "        return 1\n    def _hidden(self):\n        pass\n\ndef main(argv=None):\n    pass\n")
    assert signatures(source) == ["class Store:", "    def __init__(self, path)",
                                  "    def add(self, text: str) -> int", "def main(argv=None)"]


def test_mismatched_imports_are_repaired_before_anything_is_written(tmp_path):
    llm = ScriptedLLM(two_file_handlers(MAIN_BAD))
    report = run_cli_task(llm, tmp_path)
    assert report.status == "completed", report.error
    repair_prompt = next(c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist")
    assert "[imports]" in repair_prompt and "main_cli" in repair_prompt
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == MAIN_OK


def test_unrepaired_import_mismatch_blocks_writing(tmp_path):
    handlers = two_file_handlers(MAIN_BAD)
    handlers["debugging_specialist"] = {"summary": "no idea", "files": []}
    report = run_cli_task(ScriptedLLM(handlers), tmp_path)
    assert report.status == "failed" and not (tmp_path / "main.py").exists()


def test_developers_see_teammates_signatures(tmp_path):
    llm = ScriptedLLM(two_file_handlers(MAIN_OK))
    run_cli_task(llm, tmp_path)
    second = [c for c in llm.calls if c["role"] == "python_developer"][1]["prompt"]
    assert "- cli.py\n    def run_cli(argv=None) -> int" in second


def test_leads_are_asked_to_name_interfaces(tmp_path):
    llm = ScriptedLLM(two_file_handlers(MAIN_OK))
    run_cli_task(llm, tmp_path)
    lead = next(c for c in llm.calls if c["role"] == "tech_lead")
    assert "name the functions and classes the file must provide" in lead["system"]


# ---------------------------------------------------------------- fix 2
class Down:
    model = "down"

    def generate_json(self, *a, **k):
        from coding_agent.core.llm import LLMError
        raise LLMError("model unavailable")

    def unload(self):
        return True


def test_a_run_where_every_specialist_failed_is_reported_as_failed(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    report = CodingAgent.with_client(Down()).unit("security_architect").run(
        CodingRequest(task="threat model this", workspace=str(tmp_path)), UI)
    assert report.status == "failed"
    assert report.error.startswith("Every specialist failed (1)") and "model unavailable" in report.error


def test_partial_failures_become_warnings(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    llm = ScriptedLLM({"code_security_auditor": {"summary": "fine", "findings": []}})

    class Mixed:
        model = "mixed"

        def generate_json(self, system, prompt, **kw):
            if "Dependency Auditor" in system:
                return Down().generate_json()
            return llm.generate_json(system, prompt, **kw)

        def unload(self):
            return True

    report = CodingAgent.with_client(Mixed()).unit("security").run(
        CodingRequest(task="Audit the code and its dependencies", workspace=str(tmp_path)), UI)
    assert report.status == "completed"
    assert any(w.startswith("dependency_auditor (review) failed") for w in report.warnings)


# ---------------------------------------------------------------- fix 4
def test_target_environment_reaches_every_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("CODING_AGENT_TARGET_ENVIRONMENT", "Windows 11, Python 3.12")
    llm = ScriptedLLM({"python_developer": {"summary": "ok", "files": [
        {"path": "c.py", "action": "create", "content": CALC}]}})
    report = CodingAgent.with_client(llm).unit("python_developer").run(
        CodingRequest(task="calc", workspace=str(tmp_path)), UI)
    assert report.status == "completed"
    assert "## Target environment\nWindows 11, Python 3.12. The code must run there" in llm.calls[0]["prompt"]


def test_target_environment_defaults_to_this_machine_and_can_be_set_per_request():
    assert "Python 3." in default_target_environment()
    request = CodingRequest.from_payload({"task": "x", "workspace": "w", "target_environment": "Linux, Python 3.11"})
    assert request.target_environment == "Linux, Python 3.11"


# ---------------------------------------------------------------- fix 5
def test_new_files_stay_create_after_a_repair(tmp_path):
    report = run_cli_task(ScriptedLLM(two_file_handlers(MAIN_BAD)), tmp_path)
    assert {c.path: c.action for c in report.changes} == {"cli.py": "create", "main.py": "create"}
    assert next(c for c in report.changes if c.path == "main.py").author_role == "debugging_specialist"
