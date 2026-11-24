"""Choosing each unit's model from .env (faction > agent > division > specialist) and unit.md."""

import shutil

import pytest
from conftest import CALC, ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core.llm import ModelRouter
from coding_agent.core.registry import PACKAGE_ROOT, Tree, default_tree

TREE = default_tree()
UI = InvocationContext(source=InvocationSource.UI)


def test_variable_names_follow_the_hierarchy():
    assert TREE.root.faction == "forge"
    assert TREE.model_env_var(TREE.root) == "FORGE_CODING_AGENT_MODEL"
    assert TREE.model_env_var(TREE.get("development")) == "FORGE_CODING_AGENT_DEVELOPMENT_MODEL"
    assert TREE.model_env_var(TREE.get("python_developer")) == "FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_MODEL"
    assert TREE.model_env_var(TREE.get("ui_ux_designer")) == "FORGE_CODING_AGENT_ARCHITECTURE_UI_UX_DESIGNER_MODEL"


def test_nothing_set_means_the_role_type_default():
    assert TREE.effective_model(TREE.get("python_developer")) == (None, None)


def test_division_model_applies_to_all_its_units(monkeypatch):
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_MODEL", "dev-model")
    for unit_id in ("python_developer", "tech_lead", "development"):
        assert TREE.effective_model(TREE.get(unit_id)) == ("dev-model", ".env FORGE_CODING_AGENT_DEVELOPMENT_MODEL")
    assert TREE.effective_model(TREE.get("qa_lead")) == (None, None)


def test_more_specific_levels_win(monkeypatch):
    monkeypatch.setenv("FORGE_CODING_AGENT_MODEL", "agent-model")
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_MODEL", "dev-model")
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_MODEL", "py-model")
    assert TREE.effective_model(TREE.get("python_developer"))[0] == "py-model"
    assert TREE.effective_model(TREE.get("tech_lead"))[0] == "dev-model"
    assert TREE.effective_model(TREE.get("qa_lead"))[0] == "agent-model"


def test_blank_values_fall_through(monkeypatch):
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_MODEL", "  ")
    monkeypatch.setenv("FORGE_CODING_AGENT_MODEL", "agent-model")
    assert TREE.effective_model(TREE.get("python_developer")) == ("agent-model", ".env FORGE_CODING_AGENT_MODEL")


@pytest.fixture
def pkg(tmp_path):
    root = tmp_path / "coding_agent"
    shutil.copytree(PACKAGE_ROOT / "divisions", root / "divisions", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(PACKAGE_ROOT / "unit.md", root / "unit.md")
    return root


def set_unit_model(pkg, rel, model):
    path = pkg / "divisions" / rel / "unit.md"
    text = path.read_text(encoding="utf-8").replace("\nname:", f"\nmodel: {model}\nname:", 1)
    path.write_text(text, encoding="utf-8")


def test_specialist_unit_md_beats_division_env(pkg, monkeypatch):
    set_unit_model(pkg, "development/python_developer", "md-model")
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_MODEL", "dev-model")
    tree = Tree.load(pkg)
    assert tree.effective_model(tree.get("python_developer")) == ("md-model", "unit.md (development/python_developer)")
    assert tree.effective_model(tree.get("tech_lead"))[0] == "dev-model"


def test_env_beats_unit_md_at_the_same_level(pkg, monkeypatch):
    set_unit_model(pkg, "development", "division-md-model")
    tree = Tree.load(pkg)
    assert tree.effective_model(tree.get("python_developer"))[0] == "division-md-model"
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_MODEL", "dev-env-model")
    assert tree.effective_model(tree.get("python_developer")) == (
        "dev-env-model", ".env FORGE_CODING_AGENT_DEVELOPMENT_MODEL")


def test_older_tier_names_still_work(monkeypatch, tmp_path):
    monkeypatch.setenv("CODING_AGENT_OLLAMA_MODEL", "old-coder")
    made = {}
    router = ModelRouter(client_factory=lambda m: made.setdefault(m, ScriptedLLM(
        {"python_developer": {"summary": "ok", "files": [{"path": "c.py", "action": "create", "content": CALC}]}},
        model=m)))
    report = CodingAgent(router).unit("python_developer").run(
        CodingRequest(task="calc", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert report.outputs[0].model == "old-coder"
    assert report.outputs[0].model_source == ".env CODING_AGENT_OLLAMA_MODEL"


def test_run_uses_and_reports_the_division_model(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_CODING_AGENT_PLANNING_MODEL", "planner")
    monkeypatch.setenv("FORGE_CODING_AGENT_CODING_MODEL", "coder")
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_MODEL", "dev-model")
    made = {}
    handlers = {
        "tech_lead": {"summary": "plan", "work_items": [
            {"id": "W1", "title": "calc", "owner_role": "python_developer", "description": "", "files": ["c.py"]}]},
        "python_developer": {"summary": "ok", "files": [{"path": "c.py", "action": "create", "content": CALC}]},
    }
    router = ModelRouter(client_factory=lambda m: made.setdefault(m, ScriptedLLM(handlers, model=m)))
    report = CodingAgent(router).unit("development").run(
        CodingRequest(task="Write a calculator in Python", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert set(made) == {"dev-model"}  # the Tech Lead (planning tier) and the developer both use it
    assert {o.model_source for o in report.outputs} == {".env FORGE_CODING_AGENT_DEVELOPMENT_MODEL"}
