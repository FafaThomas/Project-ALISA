"""Gemini backend: request format, schema conversion, errors, and fallback to the local model."""

import io
import json
from urllib.error import HTTPError

import pytest
from conftest import CALC, ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core import gemini
from coding_agent.core.gemini import GeminiClient, to_gemini_schema
from coding_agent.core.llm import (
    FallbackClient, LLMError, ModelRouter, ProviderUnavailableError, RateLimitError, split_provider,
)
from coding_agent.core.prompts import FILES_SCHEMA

UI = InvocationContext(source=InvocationSource.UI)


def reply(obj, finish="STOP", thought=None):
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": json.dumps(obj)}]
    return {"candidates": [{"content": {"parts": parts}, "finishReason": finish}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    c = GeminiClient("gemini-2.5-flash", timeout=5)
    c.sent = []
    c.replies = []
    c.sleeps = []
    c.sleep = c.sleeps.append

    def fake_post(payload):
        c.sent.append(payload)
        item = c.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    c._post = fake_post
    return c


def http_error(code):
    return HTTPError("https://x", code, "err", {}, io.BytesIO(b'{"error": "nope"}'))


def test_provider_prefixes():
    assert split_provider("gemini:gemini-2.5-flash") == ("gemini", "gemini-2.5-flash")
    assert split_provider("qwen3:8b") == ("ollama", "qwen3:8b")
    assert split_provider("ollama:qwen3:8b") == ("ollama", "qwen3:8b")


def test_schema_conversion():
    converted = to_gemini_schema(FILES_SCHEMA)
    assert converted["type"] == "OBJECT" and converted["required"] == ["summary", "files"]
    files = converted["properties"]["files"]
    assert files["type"] == "ARRAY" and files["items"]["type"] == "OBJECT"
    assert files["items"]["properties"]["action"] == {"type": "STRING", "enum": ["create", "modify", "delete"]}
    assert converted["propertyOrdering"] == ["summary", "files", "notes"]


def test_request_format_and_thoughts_are_skipped(client):
    client.replies.append(reply({"summary": "ok", "files": []}, thought="let me think"))
    assert client.generate_json("SYS", "PROMPT", schema=FILES_SCHEMA, temperature=0.2) == {"summary": "ok", "files": []}
    sent = client.sent[0]
    assert sent["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert sent["contents"][0]["parts"][0]["text"] == "PROMPT"
    config = sent["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["temperature"] == 0.2
    assert config["responseSchema"]["type"] == "OBJECT"


def test_errors(client):
    client.replies += [http_error(400), reply({}, finish="MAX_TOKENS"), {"candidates": []},
                       {"promptFeedback": {"blockReason": "SAFETY"}}, http_error(429)]
    for message in ("HTTP 400", "output limit", "no candidates", "blocked"):
        with pytest.raises(LLMError, match=message):
            client.generate_json("s", "p")
    with pytest.raises(RateLimitError):
        client.generate_json("s", "p")


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY is not set"):
        GeminiClient("gemini-2.5-flash")


def test_rate_limit_falls_back_to_the_local_model(client):
    client.replies.append(http_error(429))
    local = ScriptedLLM({}, model="qwen3:8b")
    wrapped = FallbackClient(client, lambda: local, "gemini:gemini-2.5-flash")
    out = wrapped.generate_json("You are the Tech Lead on ALISA Forge", "p", schema=None)
    assert out["summary"]  # answered by the local model
    assert wrapped.used_model == "qwen3:8b" and "rate limit" in wrapped.fallback_reason


def test_units_on_gemini_report_fallbacks(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_MODEL", "gemini:gemini-2.5-flash")
    monkeypatch.setenv("FORGE_CODING_AGENT_CODING_MODEL", "local-coder")

    def fail(self, payload):
        raise http_error(429)

    monkeypatch.setattr(gemini.GeminiClient, "_post", fail)
    local = ScriptedLLM({"python_developer": {"summary": "ok", "files": [
        {"path": "c.py", "action": "create", "content": CALC}]}}, model="local-coder")

    def factory(model):
        return GeminiClient(model.split(":", 1)[1]) if model.startswith("gemini:") else local

    report = CodingAgent(ModelRouter(client_factory=factory)).unit("python_developer").run(
        CodingRequest(task="calc", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    output = report.outputs[0]
    assert output.model == "local-coder"
    assert "gemini:gemini-2.5-flash unavailable, fell back to the coding default" in output.model_source
    assert (tmp_path / "c.py").exists()


def test_units_on_gemini_use_it_when_available(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_MODEL", "gemini:gemini-2.5-flash")
    monkeypatch.setattr(gemini.GeminiClient, "_post", lambda self, payload: reply(
        {"summary": "ok", "files": [{"path": "c.py", "action": "create", "content": CALC}]}))
    report = CodingAgent(ModelRouter()).unit("python_developer").run(
        CodingRequest(task="calc", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert report.outputs[0].model == "gemini:gemini-2.5-flash"
    assert report.outputs[0].model_source == ".env FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_MODEL"


def test_overload_is_retried_then_succeeds(client):
    client.replies += [http_error(503), http_error(503), reply({"ok": True})]
    assert client.generate_json("s", "p") == {"ok": True}
    assert client.sleeps == [2.0, 5.0]


def test_persistent_overload_or_timeout_raises_provider_unavailable(client):
    client.replies += [http_error(503)] * 3
    with pytest.raises(ProviderUnavailableError, match="unavailable after 3 attempt"):
        client.generate_json("s", "p")
    client.replies += [TimeoutError("read timed out")] * 3
    with pytest.raises(ProviderUnavailableError, match="request failed"):
        client.generate_json("s", "p")


def test_client_errors_are_not_retried(client):
    client.replies.append(http_error(404))
    with pytest.raises(LLMError, match="HTTP 404") as info:
        client.generate_json("s", "p")
    assert not isinstance(info.value, ProviderUnavailableError) and client.sleeps == []


def test_overload_falls_back_to_the_local_model(client):
    client.replies += [http_error(503)] * 3
    local = ScriptedLLM({}, model="qwen3:8b")
    wrapped = FallbackClient(client, lambda: local, "gemini:gemini-2.5-flash")
    wrapped.generate_json("You are the Tech Lead on ALISA Forge", "p")
    assert wrapped.used_model == "qwen3:8b" and "unavailable after 3 attempt" in wrapped.fallback_reason
