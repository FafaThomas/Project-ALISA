"""Per-unit `think` and `temperature`, looked up like models (.env first, then unit.md, walking up)."""

import json
import shutil

import pytest
from conftest import CALC, ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core.llm import OllamaClient
from coding_agent.core.registry import PACKAGE_ROOT, RegistryError, Tree, default_tree

TREE = default_tree()
UI = InvocationContext(source=InvocationSource.UI)


def test_defaults_are_think_off_and_low_temperature():
    unit = TREE.get("python_developer")
    assert TREE.effective_think(unit) == (False, "default (off)")
    assert TREE.effective_temperature(unit) == (0.2, "default")


def test_env_settings_follow_the_hierarchy(monkeypatch):
    monkeypatch.setenv("FORGE_CODING_AGENT_ARCHITECTURE_THINK", "true")
    monkeypatch.setenv("FORGE_CODING_AGENT_TEMPERATURE", "0.5")
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_TEMPERATURE", "0")
    assert TREE.effective_think(TREE.get("software_architect")) == (True, ".env FORGE_CODING_AGENT_ARCHITECTURE_THINK")
    assert TREE.effective_think(TREE.get("python_developer"))[0] is False
    assert TREE.effective_temperature(TREE.get("python_developer"))[0] == 0.0
    assert TREE.effective_temperature(TREE.get("qa_lead")) == (0.5, ".env FORGE_CODING_AGENT_TEMPERATURE")


def test_global_temperature_fallback(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_TEMPERATURE", "0.7")
    assert TREE.effective_temperature(TREE.get("qa_lead")) == (0.7, ".env CODING_AGENT_TEMPERATURE")


def test_bad_think_value_is_reported(monkeypatch):
    monkeypatch.setenv("FORGE_CODING_AGENT_THINK", "maybe")
    with pytest.raises(RegistryError, match="THINK must be true or false"):
        TREE.effective_think(TREE.get("qa_lead"))


@pytest.fixture
def pkg(tmp_path):
    root = tmp_path / "coding_agent"
    shutil.copytree(PACKAGE_ROOT / "divisions", root / "divisions", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(PACKAGE_ROOT / "unit.md", root / "unit.md")
    return root


def test_unit_md_think_and_temperature(pkg, monkeypatch):
    path = pkg / "divisions/security/unit.md"
    path.write_text(path.read_text(encoding="utf-8").replace("\nname:", "\nthink: true\ntemperature: 0.6\nname:", 1),
                    encoding="utf-8")
    tree = Tree.load(pkg)
    auditor = tree.get("dependency_auditor")
    assert tree.effective_think(auditor) == (True, "unit.md (security)")
    assert tree.effective_temperature(auditor) == (0.6, "unit.md (security)")
    monkeypatch.setenv("FORGE_CODING_AGENT_SECURITY_DEPENDENCY_AUDITOR_THINK", "false")
    assert tree.effective_think(auditor) == (False, ".env FORGE_CODING_AGENT_SECURITY_DEPENDENCY_AUDITOR_THINK")


def test_invalid_unit_md_values_fail_on_load(pkg):
    path = pkg / "divisions/security/unit.md"
    path.write_text(path.read_text(encoding="utf-8").replace("\nname:", "\nthink: sometimes\nname:", 1),
                    encoding="utf-8")
    with pytest.raises(RegistryError, match="'think' must be true or false"):
        Tree.load(pkg)


def test_specialists_pass_think_and_temperature_to_the_model(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_THINK", "true")
    llm = ScriptedLLM({"python_developer": {"summary": "ok", "files": [
        {"path": "c.py", "action": "create", "content": CALC}]}})
    CodingAgent.with_client(llm).unit("python_developer").run(CodingRequest(task="calc", workspace=str(tmp_path)), UI)
    assert (llm.calls[0]["think"], llm.calls[0]["temperature"]) == (True, 0.2)


def test_ollama_payload_includes_think_and_temperature(monkeypatch):
    sent = {}

    class FakeResponse:
        def __init__(self, lines):
            self.lines = lines

        def __enter__(self):
            return iter(self.lines)

        def __exit__(self, *a):
            return False

    def fake_post(self, path, payload, timeout):
        sent.update(payload)
        return FakeResponse([json.dumps({"message": {"content": '{"ok": true}'}, "done": True}).encode()])

    monkeypatch.setattr(OllamaClient, "_post", fake_post)
    client = OllamaClient("m", timeout=5, num_predict=10, num_ctx=100)
    assert client.generate_json("s", "p", think=False, temperature=0.2) == {"ok": True}
    assert sent["think"] is False and sent["options"]["temperature"] == 0.2
    sent.clear()
    client.generate_json("s", "p")
    assert "think" not in sent and "temperature" not in sent["options"]
