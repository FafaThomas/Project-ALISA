from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from coding_agent.llm_client import LLMError, OllamaClient


def _client(monkeypatch, **kwargs):
    monkeypatch.setattr("coding_agent.llm_client.load_local_env", lambda: None)
    monkeypatch.setenv("CODING_AGENT_OLLAMA_MODEL", "qwen2.5-coder:14b")
    return OllamaClient(**kwargs)


def test_timeout_defaults_to_five_minutes(monkeypatch):
    client = _client(monkeypatch)
    assert client.timeout == 300.0


def test_timeout_uses_environment_setting(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_LLM_TIMEOUT", "420")
    client = _client(monkeypatch)
    assert client.timeout == 420.0


def test_explicit_timeout_overrides_environment_setting(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_LLM_TIMEOUT", "420")
    client = _client(monkeypatch, timeout=90)
    assert client.timeout == 90.0


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_invalid_environment_timeout_is_rejected(monkeypatch, value):
    monkeypatch.setenv("CODING_AGENT_LLM_TIMEOUT", value)
    with pytest.raises(ValueError, match="CODING_AGENT_LLM_TIMEOUT"):
        _client(monkeypatch)


def test_nonpositive_explicit_timeout_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="greater than zero"):
        _client(monkeypatch, timeout=0)


def test_generate_json_records_duration_and_logs_success(monkeypatch, caplog):
    client = _client(monkeypatch)

    class Response:
        def __init__(self):
            self.lines = [
                json.dumps({
                    "message": {"content": '{"ok": true}'},
                    "done": True,
                    "done_reason": "stop",
                }).encode() + b"\n"
            ]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def readline(self):
            return self.lines.pop(0) if self.lines else b""

    monkeypatch.setattr("coding_agent.llm_client.urlopen", lambda *_a, **_k: Response())

    with caplog.at_level("INFO", logger="coding_agent.llm_client"):
        assert client.generate_json("return json") == {"ok": True}

    assert client.last_request_duration_seconds is not None
    assert client.last_request_duration_seconds >= 0
    assert "Ollama request completed in" in caplog.text


def test_timeout_error_is_reported_as_inference_timeout(monkeypatch):
    client = _client(monkeypatch, timeout=12)

    def timeout(*_args, **_kwargs):
        raise URLError(TimeoutError("socket timed out"))

    monkeypatch.setattr("coding_agent.llm_client.urlopen", timeout)

    with pytest.raises(LLMError, match=r"request timed out.*12.0s"):
        client.generate_json("return json")

    assert client.last_request_duration_seconds is not None


def test_connection_error_is_not_reported_as_timeout(monkeypatch):
    client = _client(monkeypatch)

    def connection_error(*_args, **_kwargs):
        raise URLError(ConnectionError("connection refused"))

    monkeypatch.setattr("coding_agent.llm_client.urlopen", connection_error)

    with pytest.raises(LLMError, match="Could not connect to Ollama"):
        client.generate_json("return json")


def test_cli_accepts_separate_llm_timeout():
    from coding_agent.cli import build_parser

    args = build_parser().parse_args([
        "run", "workspace", "task", "--timeout", "180", "--llm-timeout", "360"
    ])

    assert args.timeout == 180.0
    assert args.llm_timeout == 360.0


def test_cli_rejects_nonpositive_llm_timeout(tmp_path, capsys):
    from coding_agent.cli import main

    result = main([
        "run", str(tmp_path), "task", "--llm-timeout", "0"
    ])

    assert result == 2
    assert "--llm-timeout must be greater than zero" in capsys.readouterr().err
