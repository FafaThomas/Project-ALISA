"""Each unit folder's agent.py / api.py / cli.py and the hooks in agent.py."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CALC, ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core.llm import ModelRouter
from coding_agent.core.registry import PACKAGE_ROOT, Tree, default_tree
from coding_agent.core.scaffold import check, class_name, scaffold
from coding_agent.unit_entry import UnitAgent, load_unit_agent, locate_unit

UI = InvocationContext(source=InvocationSource.UI)
SRC = PACKAGE_ROOT.parent


@pytest.fixture
def pkg(tmp_path):
    root = tmp_path / "pkg" / "coding_agent"
    shutil.copytree(PACKAGE_ROOT / "divisions", root / "divisions",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("unit.md", "agent.py", "api.py", "cli.py"):
        shutil.copy(PACKAGE_ROOT / name, root / name)
    return root


def test_every_packaged_unit_has_its_entry_files():
    assert check(default_tree()) == []
    folder = PACKAGE_ROOT / "divisions" / "development" / "python_developer"
    assert sorted(p.name for p in folder.iterdir() if p.suffix in (".py", ".md")) == \
        ["agent.py", "api.py", "cli.py", "unit.md"]


def test_entry_files_locate_their_own_unit():
    folder = PACKAGE_ROOT / "divisions" / "security" / "dependency_auditor"
    assert locate_unit(folder / "cli.py") == (PACKAGE_ROOT, "security/dependency_auditor")
    assert locate_unit(PACKAGE_ROOT / "divisions" / "security" / "api.py") == (PACKAGE_ROOT, "security")
    assert locate_unit(PACKAGE_ROOT / "agent.py") == (PACKAGE_ROOT, "")


def test_scaffold_creates_files_for_a_new_unit_once(pkg):
    folder = pkg / "divisions" / "devops" / "terraform_engineer"
    folder.mkdir()
    (folder / "unit.md").write_text("---\nid: terraform_engineer\nname: Terraform Engineer\nkind: builder\n"
                                    "responsibility: Terraform\n---\n# T\n", encoding="utf-8")
    tree = Tree.load(pkg)
    assert any(p.startswith("devops/terraform_engineer/") for p in check(tree))
    written = scaffold(tree)
    assert sorted(p.name for p in written) == ["agent.py", "api.py", "cli.py"]
    assert check(tree) == []
    assert "class TerraformEngineer(UnitAgent):" in (folder / "agent.py").read_text(encoding="utf-8")
    assert scaffold(tree) == []                    # existing files are kept
    assert len(scaffold(tree, [tree.get("terraform_engineer")], force=True)) == 3


def test_check_flags_entry_files_not_built_on_unit_entry(pkg):
    (pkg / "divisions" / "security" / "dependency_auditor" / "api.py").write_text("print('hi')\n", encoding="utf-8")
    assert check(Tree.load(pkg)) == ["security/dependency_auditor/api.py: does not use coding_agent.unit_entry"]


@pytest.mark.parametrize("name, expected", [
    ("UI/UX Designer", "UiUxDesigner"), (".NET Developer", "NetDeveloper"),
    ("E2E Testing Engineer", "E2eTestingEngineer"), ("Python Developer", "PythonDeveloper"),
])
def test_class_names(name, expected):
    unit = default_tree().get("python_developer")
    assert class_name(type(unit)(**{**unit.__dict__, "name": name})) == expected


def test_entry_scripts_run_from_their_folders():
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    folder = PACKAGE_ROOT / "divisions" / "development" / "python_developer"
    for script, expected in (("cli.py", "coding-agent run"), ("api.py", "coding-agent serve")):
        result = subprocess.run([sys.executable, str(folder / script), "--help"], capture_output=True, text=True,
                                env=env, timeout=60)
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout


# ------------------------------------------------------------------- hooks
HOOKED_AGENT = '''
from coding_agent.unit_entry import UnitAgent


class PythonDeveloper(UnitAgent):
    unit_file = __file__

    def extra_context(self, query, workspace_root):
        return "HOUSE RULE: use pathlib"

    def after_output(self, output, action):
        if action == "build":
            for change in output.changes:
                if "print(" in change.content:
                    output.concerns.append(f"{change.path} prints to stdout")
        return output
'''

HOOKED_REVIEWER = '''
from coding_agent.unit_entry import UnitAgent


class QaLead(UnitAgent):
    unit_file = __file__

    def review(self, files):
        return [{"severity": "low", "file": p, "issue": "missing module docstring"}
                for p, c in files if p.endswith(".py") and not c.lstrip().startswith('"""')]
'''


def test_agent_py_hooks_change_how_one_unit_works(pkg, tmp_path):
    (pkg / "divisions/development/python_developer/agent.py").write_text(HOOKED_AGENT, encoding="utf-8")
    (pkg / "divisions/quality/qa_lead/agent.py").write_text(HOOKED_REVIEWER, encoding="utf-8")
    llm = ScriptedLLM({
        "engineering_director": {"rationale": "x", "team": ["python_developer", "qa_lead"]},
        "tech_lead": {"summary": "one", "work_items": [
            {"id": "W1", "title": "calc", "owner_role": "python_developer", "description": "", "files": ["calc.py"]}]},
        "python_developer": {"summary": "done", "files": [
            {"path": "calc.py", "action": "create", "content": CALC + "print(add(1, 2))\n"}]},
    })
    agent = CodingAgent.with_client(llm, tree=Tree.load(pkg))
    report = agent.run(CodingRequest(task="Build a calculator in Python", workspace=str(tmp_path / "w"),
                                     acceptance_criteria=["adds"]), UI)
    assert report.status == "completed", report.error
    dev_call = next(c for c in llm.calls if c["role"] == "python_developer")
    assert "HOUSE RULE: use pathlib" in dev_call["prompt"]                       # extra_context
    dev_output = next(o for o in report.outputs if o.role == "python_developer")
    assert "calc.py prints to stdout" in dev_output.concerns                       # after_output
    assert any(f.issue == "missing module docstring" for f in report.advisory_findings)  # review


def test_broken_agent_py_does_not_break_the_run(pkg, tmp_path):
    (pkg / "divisions/development/python_developer/agent.py").write_text("raise RuntimeError('boom')\n",
                                                                          encoding="utf-8")
    llm = ScriptedLLM({"python_developer": {"summary": "ok", "files": [
        {"path": "calc.py", "action": "create", "content": CALC}]}})
    agent = CodingAgent.with_client(llm, tree=Tree.load(pkg))
    report = agent.unit("python_developer").run(CodingRequest(task="calc", workspace=str(tmp_path / "w")), UI)
    assert report.status == "completed", report.error


def test_unit_agent_runs_its_own_unit(pkg, tmp_path):
    (pkg / "divisions/development/python_developer/agent.py").write_text(HOOKED_AGENT, encoding="utf-8")
    llm = ScriptedLLM({"python_developer": {"summary": "ok", "files": [
        {"path": "calc.py", "action": "create", "content": CALC}]}})
    unit = load_unit_agent("python_developer", tree=Tree.load(pkg), router=ModelRouter.single(llm))
    assert isinstance(unit, UnitAgent) and type(unit).__name__ == "PythonDeveloper"
    assert unit.unit_path == "development/python_developer"
    report = unit.run(CodingRequest(task="calc", workspace=str(tmp_path / "w")), UI)
    assert report.status == "completed" and report.unit_id == "python_developer"
    assert (tmp_path / "w" / "calc.py").exists()
