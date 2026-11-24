"""Quota-aware routing for hosted models: per-minute, per-day and provider-imposed limits."""

import io
import json
from urllib.error import HTTPError

import pytest
from conftest import ScriptedLLM

from coding_agent.core import quota
from coding_agent.core.gemini import GeminiClient
from coding_agent.core.llm import FallbackClient, LLMError, ModelRouter, resolve_alias
from coding_agent.core.quota import QuotaExhaustedError, QuotaLimits, QuotaTracker, limits_for, parse_rate_limit


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def qt(tmp_path, clock):
    slept = []
    tracker = QuotaTracker(tmp_path / "usage.json", clock=clock, sleep=slept.append)
    tracker.slept = slept
    return tracker


def test_limits_come_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_QUOTA_GEMINI_4_HYDROGEN", "5,250000,20")
    assert limits_for("gemini", "gemini-4-hydrogen") == QuotaLimits(5, 250000, 20)
    assert limits_for("gemini", "gemini-3.5-flash-lite") == QuotaLimits()
    monkeypatch.setenv("GEMINI_QUOTA_GEMMA_4_31B_IT", "30,,")
    assert limits_for("gemini", "gemma-4-31b-it") == QuotaLimits(30, 0, 0)


def test_requests_per_day_are_enforced_and_persisted(qt, tmp_path, clock):
    limits = QuotaLimits(rpd=2)
    for _ in range(2):
        qt.before_call("m", limits, 10)
        qt.record("m", 10)
    with pytest.raises(QuotaExhaustedError, match="2/2 requests today"):
        qt.before_call("m", limits, 10)
    again = QuotaTracker(tmp_path / "usage.json", clock=clock)  # a new process sees the same count
    with pytest.raises(QuotaExhaustedError):
        again.before_call("m", limits, 10)
    clock.t += 24 * 3600  # next quota day
    again.before_call("m", limits, 10)


def test_short_per_minute_waits_are_slept_long_ones_refused(qt, clock, monkeypatch):
    limits = QuotaLimits(rpm=2)
    qt.record("m", 1)
    clock.t += 50
    qt.record("m", 1)
    qt.before_call("m", limits, 1)  # oldest call leaves the window in 10 s: wait it out
    assert qt.slept == [pytest.approx(10)]
    monkeypatch.setenv("GEMINI_MAX_WAIT", "5")
    with pytest.raises(QuotaExhaustedError, match="per-minute limit"):
        qt.before_call("m", limits, 1)


def test_tokens_per_minute(qt, monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_WAIT", "0")
    qt.record("m", 15_000)
    with pytest.raises(QuotaExhaustedError):
        qt.before_call("m", QuotaLimits(tpm=16_000), 2_000)
    qt.before_call("m", QuotaLimits(tpm=16_000), 500)


def test_provider_benches_stop_requests(qt, clock):
    qt.bench("m", 30)
    with pytest.raises(QuotaExhaustedError, match="benched"):
        qt.before_call("m", QuotaLimits(), 1)
    clock.t += 31
    qt.before_call("m", QuotaLimits(), 1)
    qt.bench("m", None, daily=True)
    clock.t += 3600
    with pytest.raises(QuotaExhaustedError):
        qt.before_call("m", QuotaLimits(), 1)


def test_parse_rate_limit_bodies():
    body = json.dumps({"error": {"code": 429, "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"}]}})
    assert parse_rate_limit(body) == (34.0, True)
    assert parse_rate_limit('{"error": {"message": "slow down"}}') == (None, False)


def test_gemini_429_benches_the_model_so_the_next_call_skips_the_network(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def post(self, payload):
        calls.append(payload)
        body = b'{"error": {"details": [{"retryDelay": "40s"}]}}'
        raise HTTPError("u", 429, "x", {}, io.BytesIO(body))

    monkeypatch.setattr(GeminiClient, "_post", post)
    wrapped = FallbackClient(GeminiClient("gemini-x"), lambda: ScriptedLLM({}, model="local"), "gemini:gemini-x")
    wrapped.generate_json("You are the Tech Lead on ALISA Forge", "p")
    wrapped.generate_json("You are the Tech Lead on ALISA Forge", "p")
    assert len(calls) == 1  # second call was refused locally and went straight to the fallback
    assert wrapped.used_model == "local" and "benched" in wrapped.fallback_reason


def test_token_usage_is_reported(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(GeminiClient, "_post", lambda self, payload: {
        "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 5,
                          "totalTokenCount": 125}})
    client = GeminiClient("gemini-x")
    client.generate_json("s", "p")
    assert client.last_usage == {"prompt": 100, "output": 20, "thinking": 5, "total": 125}
    assert quota.tracker().usage("gemini:gemini-x")["tokens"] == 125


def test_aliases(monkeypatch):
    monkeypatch.setenv("FORGE_GEMINI_REASONING_MODEL", "gemini-4-hydrogen")
    assert resolve_alias("gemini:@reasoning") == ("gemini:gemini-4-hydrogen", ".env FORGE_GEMINI_REASONING_MODEL")
    assert resolve_alias("gemini:gemini-2.5-flash") == ("gemini:gemini-2.5-flash", "")
    with pytest.raises(LLMError, match="FORGE_GEMINI_LITE_MODEL"):
        resolve_alias("gemini:@lite")
    client, source = ModelRouter(client_factory=lambda m: ScriptedLLM({}, model=m)).resolve(
        "planning", "gemini:@reasoning", ".env FORGE_CODING_AGENT_LEADERSHIP_MODEL")
    assert client.primary.model == "gemini:gemini-4-hydrogen"
    assert source == ".env FORGE_CODING_AGENT_LEADERSHIP_MODEL -> .env FORGE_GEMINI_REASONING_MODEL"
