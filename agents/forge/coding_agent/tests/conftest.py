from __future__ import annotations

import re
from typing import Any, Callable

import pytest

from coding_agent.core.registry import default_tree

_NAME_TO_ID = {unit.name: unit.id for unit in default_tree().specialists}
_ROLE_RE = re.compile(r"^You are the (.+?) (?:on|of) ALISA Forge")

Handler = Callable[[str, dict | None], dict]


class ScriptedLLM:
    """Fake LLM that answers per role, recording every call."""

    def __init__(self, handlers: dict[str, Handler | dict] | None = None, model: str = "fake") -> None:
        self.model = model
        self.handlers = handlers or {}
        self.calls: list[dict[str, Any]] = []
        self.unloads = 0

    def unload(self) -> bool:
        self.unloads += 1
        return True

    def generate_json(self, system: str, prompt: str, *, schema=None, think=None, temperature=None) -> dict:
        match = _ROLE_RE.match(system)
        # Units outside the packaged tree (test trees) map "Postgres Developer" -> "postgres_developer".
        role = (_NAME_TO_ID.get(match.group(1)) or match.group(1).lower().replace(" ", "_")) if match else "unknown"
        self.calls.append({"role": role, "system": system, "prompt": prompt, "schema": schema,
                           "think": think, "temperature": temperature})
        handler = self.handlers.get(role)
        if handler is None:
            return default_reply(role, prompt, schema)
        if callable(handler):
            return handler(prompt, schema)
        return handler

    def roles_called(self) -> list[str]:
        return [c["role"] for c in self.calls]


def default_reply(role: str, prompt: str, schema) -> dict:
    props = (schema or {}).get("properties", {})
    if "team" in props:
        return {"rationale": "keep suggestion", "team": []}
    if "work_items" in props:
        return {"summary": "plan", "work_items": []}
    if "files" in props:
        return {"summary": "nothing", "files": []}
    if "findings" in props:
        return {"summary": "looks fine", "findings": []}
    return {"summary": f"{role} notes", "decisions": [f"{role} decision"], "concerns": []}


CALC = 'def add(a, b):\n    return a + b\n\n\ndef divide(a, b):\n    if b == 0:\n        raise ZeroDivisionError("b must not be zero")\n    return a / b\n'
MAIN = 'from calculator import add\n\n\nif __name__ == "__main__":\n    print(add(2, 3))\n'


def calculator_handlers(builder: str = "python_developer") -> dict[str, Any]:
    return {
        "engineering_director": {"rationale": "small python tool", "team": ["tech_lead", builder, "software_architect"]},
        "tech_lead": {
            "summary": "two modules",
            "work_items": [
                {"id": "W1", "title": "Calculator core", "owner_role": builder,
                 "description": "arithmetic functions", "files": ["calculator.py"]},
                {"id": "W2", "title": "Entry point", "owner_role": builder,
                 "description": "CLI entry", "files": ["main.py"]},
            ],
        },
        builder: lambda prompt, schema: (
            {"summary": "core", "files": [{"path": "calculator.py", "action": "create", "content": CALC}]}
            if "Your work item W1" in prompt else
            {"summary": "entry", "files": [{"path": "main.py", "action": "create", "content": MAIN}]}
        ),
    }


@pytest.fixture
def scripted():
    return ScriptedLLM


@pytest.fixture(autouse=True)
def isolated_model_env(monkeypatch, tmp_path):
    """Keep the developer's .env, shell model settings and quota usage file out of the tests."""
    import os

    from coding_agent.core import env

    monkeypatch.setattr(env, "find_project_env", lambda: None)
    for name in list(os.environ):
        if name.startswith(("FORGE_", "GEMINI_")) or "OLLAMA" in name:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_USAGE_FILE", str(tmp_path / "gemini_usage.json"))
    # Tests that exercise the Debugging Specialist's test run turn it on explicitly.
    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "off")
