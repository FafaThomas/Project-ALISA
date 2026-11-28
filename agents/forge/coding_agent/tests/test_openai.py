"""OpenAI-compatible backend: request format, adapting to what a model accepts, errors, and fallback."""

import io
import json
from urllib.error import HTTPError

import pytest
from conftest import CALC, ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core import openai_compat
from coding_agent.core.llm import (
    FallbackClient, LLMError, ModelRouter, OutputLimitError, ProviderUnavailableError, RateLimitError,
    default_client_factory, resolve_alias, split_provider,
)
from coding_agent.core.openai_compat import OpenAICompatClient, _duration
from coding_agent.core.prompts import FILES_SCHEMA
from coding_agent.core.quota import tracker

UI = InvocationContext(source=InvocationSource.UI)


def reply(obj, finish="stop", reasoning=0):
    return {"choices": [{"message": {"content": json.dumps(obj)}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                      "completion_tokens_details": {"reasoning_tokens": reasoning}}}


def http_error(code, error=None, headers=None):
    body = json.dumps({"error": error or {"message": "nope"}}).encode()
    return HTTPError("https://x", code, "err", headers or {}, io.BytesIO(body))


@pytest.fixture
def client(monkeypatch):
    for name in ("OPENAI_API_BASE", "OPENAI_REASONING_EFFORT", "OPENAI_MAX_OUTPUT_TOKENS", "OPENAI_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    c = OpenAICompatClient("big-model", timeout=5)
    c.sent, c.replies, c.sleeps = [], [], []
    c.sleep = c.sleeps.append

    def fake_post(payload):
        c.sent.append(json.loads(json.dumps(payload)))
        item = c.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    c._post = fake_post
    return c


def test_the_openai_prefix_and_named_models(monkeypatch):
    assert split_provider("openai:gpt-x") == ("openai", "gpt-x")
    monkeypatch.setenv("FORGE_OPENAI_REASONING_MODEL", "gpt-x")
    assert resolve_alias("openai:@reasoning") == ("openai:gpt-x", ".env FORGE_OPENAI_REASONING_MODEL")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert isinstance(default_client_factory("openai:gpt-x"), OpenAICompatClient)


def test_request_format_usage_and_reply(client):
    client.replies.append(reply({"summary": "ok", "files": []}, reasoning=7))
    assert client.generate_json("SYS", "PROMPT", schema=FILES_SCHEMA, temperature=0.2) == {"summary": "ok", "files": []}
    sent = client.sent[0]
    assert sent["model"] == "big-model" and sent["temperature"] == 0.2
    assert sent["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "PROMPT"}]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"] == FILES_SCHEMA
    assert sent["response_format"]["json_schema"]["strict"] is False
    assert "reasoning_effort" not in sent and "max_completion_tokens" not in sent
    assert client.last_usage == {"prompt": 100, "output": 20, "thinking": 7, "total": 120}


def test_reasoning_effort_and_output_cap_come_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "32000")
    sent = OpenAICompatClient("m")._payload("s", "p", None, None)
    assert sent["reasoning_effort"] == "high" and sent["max_completion_tokens"] == 32000


def test_a_refused_parameter_is_dropped_and_stays_dropped(client):
    client.replies += [
        http_error(400, {"message": "Unsupported value: 'temperature' does not support 0.2 with this model.",
                         "param": "temperature", "code": "unsupported_value"}),
        reply({"a": 1}), reply({"b": 2})]
    assert client.generate_json("s", "p", schema=FILES_SCHEMA, temperature=0.2) == {"a": 1}
    assert "temperature" in client.sent[0] and "temperature" not in client.sent[1]
    client.generate_json("s", "p", schema=FILES_SCHEMA, temperature=0.2)
    assert "temperature" not in client.sent[2] and client.sleeps == []


def test_without_json_schema_support_the_schema_goes_into_the_prompt(client):
    client.replies += [
        http_error(400, {"message": "response_format json_schema is not supported", "param": "response_format"}),
        reply({"summary": "ok", "files": []})]
    client.generate_json("SYS", "p", schema=FILES_SCHEMA)
    retry = client.sent[1]
    assert retry["response_format"] == {"type": "json_object"}
    system = retry["messages"][0]["content"]
    assert system.startswith("SYS") and "JSON Schema" in system and '"files"' in system


def test_without_any_response_format_the_text_is_parsed(client):
    client.schema_mode = "json_object"
    client.replies += [http_error(400, {"message": "unknown field response_format"}),
                       {"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}, "finish_reason": "stop"}]}]
    assert client.generate_json("s", "p", schema=FILES_SCHEMA) == {"ok": True}
    assert "response_format" not in client.sent[1] and "JSON object" in client.sent[1]["messages"][0]["content"]


def test_other_bad_requests_are_errors_not_retries(client):
    client.replies.append(http_error(400, {"message": "This model's maximum context length is 128000 tokens"}))
    with pytest.raises(LLMError, match="HTTP 400") as info:
        client.generate_json("s", "p")
    assert not isinstance(info.value, ProviderUnavailableError)


def test_reply_errors(client):
    client.replies += [reply({}, finish="length"), {"choices": []},
                       {"choices": [{"message": {"content": None, "refusal": "I can't"}, "finish_reason": "stop"}]}]
    with pytest.raises(OutputLimitError):
        client.generate_json("s", "p")
    for message in ("no choices", "refused"):
        with pytest.raises(LLMError, match=message):
            client.generate_json("s", "p")


def test_overload_is_retried_then_falls_back(client):
    client.replies += [http_error(503), http_error(502), reply({"ok": True})]
    assert client.generate_json("s", "p") == {"ok": True}
    assert client.sleeps == [2.0, 5.0]
    client.replies += [TimeoutError("read timed out")] * 3
    with pytest.raises(ProviderUnavailableError, match="unavailable after 3 attempt"):
        client.generate_json("s", "p")


def test_a_short_rate_limit_is_waited_out_a_long_one_falls_back(client):
    client.replies += [http_error(429, headers={"retry-after": "3"}), reply({"ok": True})]
    assert client.generate_json("s", "p") == {"ok": True}
    assert client.sleeps == [3.0]
    client.replies.append(http_error(429, headers={"x-ratelimit-reset-requests": "6m0s"}))
    with pytest.raises(RateLimitError):
        client.generate_json("s", "p")
    assert tracker().usage("openai:big-model")["benched_until"] > 0  # later calls go straight to the fallback


def test_a_spent_balance_benches_the_model(client):
    client.replies.append(http_error(429, {"message": "You exceeded your current quota", "code": "insufficient_quota"}))
    with pytest.raises(RateLimitError):
        client.generate_json("s", "p")
    assert tracker().usage("openai:big-model").get("exhausted") is True


@pytest.mark.parametrize("code", [401, 403, 404])
def test_a_rejected_key_or_unknown_model_falls_back(client, code):
    client.replies.append(http_error(code))
    local = ScriptedLLM({}, model="qwen3:8b")
    wrapped = FallbackClient(client, lambda: local, "openai:big-model")
    assert wrapped.generate_json("You are the Tech Lead on ALISA Forge", "p")["summary"]
    assert wrapped.used_model == "qwen3:8b" and f"HTTP {code}" in wrapped.fallback_reason


def test_a_missing_key_falls_back_but_a_local_server_needs_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    with pytest.raises(ProviderUnavailableError, match="OPENAI_API_KEY is not set"):
        OpenAICompatClient("m").generate_json("s", "p")
    local_server = OpenAICompatClient("m", base_url="http://127.0.0.1:1234/v1")
    local_server._post = lambda payload: reply({"ok": True})
    assert local_server.generate_json("s", "p") == {"ok": True}


def test_openai_down_tries_gemini_then_the_local_model(monkeypatch):
    monkeypatch.setenv("FORGE_OPENAI_FALLBACK_MODEL", "gemini:@general")
    monkeypatch.setenv("FORGE_GEMINI_GENERAL_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("FORGE_CODING_AGENT_PLANNING_MODEL", "qwen3:8b")
    up = {"gemini:gemini-3.5-flash-lite", "qwen3:8b"}

    class Down:
        def __init__(self, model):
            self.model = model

        def generate_json(self, system, prompt, **kwargs):
            raise ProviderUnavailableError(f"{self.model}: OPENAI_API_KEY is not set")

        def unload(self):
            return True

    def factory(model):
        return ScriptedLLM({}, model=model) if model in up else Down(model)

    client, _ = ModelRouter(client_factory=factory).resolve("planning", "openai:gpt-x", "unit")
    client.generate_json("You are the Engineering Director of ALISA Forge", "p", schema=None)
    assert client.used_model == "gemini:gemini-3.5-flash-lite" and client.fallback_kind == "hosted"
    assert "OPENAI_API_KEY is not set" in client.fallback_reason
    up.discard("gemini:gemini-3.5-flash-lite")
    client, _ = ModelRouter(client_factory=factory).resolve("planning", "openai:gpt-x", "unit")
    client.generate_json("You are the Engineering Director of ALISA Forge", "p", schema=None)
    assert client.used_model == "qwen3:8b" and client.fallback_kind == "local"
    monkeypatch.setenv("FORGE_OPENAI_FALLBACK_MODEL", "qwen3:8b")  # local: the tier fallback covers that
    from coding_agent.core.llm import hosted_fallback_model
    assert hosted_fallback_model("openai") == "openai:qwen3:8b"  # no prefix: read as the same provider's model
    monkeypatch.setenv("FORGE_OPENAI_FALLBACK_MODEL", "ollama:qwen3:8b")
    assert hosted_fallback_model("openai") is None


def test_rate_limit_durations():
    assert [_duration(t) for t in ("20", "1.5s", "6m0s", "250ms", "1h2m", "", "soon")] == [
        20.0, 1.5, 360.0, 0.25, 3720.0, None, None]


def test_units_on_openai_use_it_and_fall_back_when_it_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("FORGE_OPENAI_REASONING_MODEL", "big-model")
    monkeypatch.setenv("FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_MODEL", "openai:@reasoning")
    monkeypatch.setenv("FORGE_CODING_AGENT_CODING_MODEL", "local-coder")
    files = {"summary": "ok", "files": [{"path": "c.py", "action": "create", "content": CALC}]}
    monkeypatch.setattr(openai_compat.OpenAICompatClient, "_post", lambda self, payload: reply(files))
    report = CodingAgent(ModelRouter()).unit("python_developer").run(
        CodingRequest(task="calc", workspace=str(tmp_path / "a")), UI)
    assert report.status == "completed", report.error
    assert report.outputs[0].model == "openai:big-model"
    assert report.outputs[0].model_source == (".env FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_MODEL -> "
                                              ".env FORGE_OPENAI_REASONING_MODEL")

    def unavailable(self, payload):
        raise http_error(503)

    monkeypatch.setattr(openai_compat.OpenAICompatClient, "_post", unavailable)
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    local = ScriptedLLM({"python_developer": files}, model="local-coder")

    def factory(model):
        return OpenAICompatClient(model.split(":", 1)[1]) if model.startswith("openai:") else local

    report = CodingAgent(ModelRouter(client_factory=factory)).unit("python_developer").run(
        CodingRequest(task="calc", workspace=str(tmp_path / "b")), UI)
    assert report.status == "completed", report.error
    assert report.outputs[0].model == "local-coder"
    assert "openai:big-model unavailable, fell back to the coding default" in report.outputs[0].model_source
