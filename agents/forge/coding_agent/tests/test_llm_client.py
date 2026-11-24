import json

import pytest

from coding_agent.llm_client import OllamaClient


def test_coding_agent_model_setting_takes_precedence(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_OLLAMA_MODEL", "qwen2.5-coder:14b")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    client = OllamaClient()

    assert client.model == "qwen2.5-coder:14b"


def test_shared_model_is_used_as_fallback(monkeypatch):
    monkeypatch.setattr(
        "coding_agent.llm_client.load_local_env",
        lambda: None,
    )
    monkeypatch.delenv("CODING_AGENT_OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    client = OllamaClient()

    assert client.model == "qwen2.5:14b"


def test_explicit_model_argument_has_highest_precedence(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_OLLAMA_MODEL", "qwen2.5-coder:14b")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    client = OllamaClient(model="llama3.1:8b")

    assert client.model == "llama3.1:8b"


def test_planning_role_prefers_planning_model(monkeypatch):
    monkeypatch.setenv("PLANNING_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("CODING_AGENT_OLLAMA_MODEL", "qwen2.5-coder:7b")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    client = OllamaClient(role="planning")

    assert client.model == "qwen3:8b"
    assert client.role == "planning"


def test_planning_role_falls_back_to_shared_model(monkeypatch):
    monkeypatch.setattr("coding_agent.llm_client.load_local_env", lambda: None)
    monkeypatch.delenv("PLANNING_OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    client = OllamaClient(role="planning")

    assert client.model == "qwen2.5:14b"


def test_explicit_model_overrides_role_environment(monkeypatch):
    monkeypatch.setenv("PLANNING_OLLAMA_MODEL", "qwen3:8b")

    client = OllamaClient(model="custom-model:latest", role="planning")

    assert client.model == "custom-model:latest"


def test_rejects_unknown_model_role():
    with pytest.raises(ValueError, match="role must be either"):
        OllamaClient(model="test-model", role="review")


def test_unload_requests_immediate_model_release(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("coding_agent.llm_client.urlopen", fake_urlopen)
    client = OllamaClient(model="planner-model", base_url="http://localhost:11434")

    assert client.unload() is True
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["payload"] == {"model": "planner-model", "keep_alive": 0}


class _StreamingResponse:
    def __init__(self, records):
        self._lines = [json.dumps(item).encode("utf-8") + b"\n" for item in records]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def readline(self):
        return self._lines.pop(0) if self._lines else b""


def test_generate_json_streams_chunks_and_applies_output_limit(monkeypatch):
    import json

    from coding_agent.llm_client import OllamaClient

    records = [
        {"message": {"content": '{"answer":'}, "done": False},
        {"message": {"content": '"ok"}'}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 12},
    ]
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _StreamingResponse(records)

    monkeypatch.setattr("coding_agent.llm_client.urlopen", fake_urlopen)
    client = OllamaClient(model="test-model", timeout=7, num_predict=321)

    assert client.generate_json("return a small JSON object") == {"answer": "ok"}
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["options"]["num_predict"] == 321
    assert captured["timeout"] == 7


def test_generate_json_rejects_output_truncated_by_num_predict(monkeypatch):
    import json
    from coding_agent.llm_client import LLMError, OllamaClient

    records = [
        {"message": {"content": '{"answer":'}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "length"},
    ]
    monkeypatch.setattr(
        "coding_agent.llm_client.urlopen",
        lambda request, timeout: _StreamingResponse(records),
    )
    client = OllamaClient(model="test-model", timeout=7, num_predict=64)

    with pytest.raises(LLMError, match="reached CODING_AGENT_OLLAMA_NUM_PREDICT"):
        client.generate_json("return a JSON object")


def test_num_predict_environment_setting_is_validated(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_OLLAMA_NUM_PREDICT", "2048")
    client = OllamaClient(model="test-model")
    assert client.num_predict == 2048

    monkeypatch.setenv("CODING_AGENT_OLLAMA_NUM_PREDICT", "not-a-number")
    with pytest.raises(ValueError, match="CODING_AGENT_OLLAMA_NUM_PREDICT"):
        OllamaClient(model="test-model")
