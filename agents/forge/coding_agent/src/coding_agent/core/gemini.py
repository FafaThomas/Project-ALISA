"""Google Gemini (AI Studio API) client for units whose model is ``gemini:<model>``.

    FORGE_CODING_AGENT_ARCHITECTURE_MODEL=gemini:gemini-2.5-flash

Environment:

- ``GEMINI_API_KEY``    required; the AI Studio key
- ``GEMINI_API_BASE``   optional; default https://generativelanguage.googleapis.com/v1beta
- ``GEMINI_TIMEOUT``    seconds per request (default 120)
- ``GEMINI_RETRIES``    retries on overload/timeout before giving up (default 2)
- ``GEMINI_QUOTA_<MODEL>``  rpm,tpm,rpd limits for quota-aware routing (see core/quota.py)

Uses Gemini's JSON mode with a response schema, so replies have the same
shape as the Ollama models'. Gemini decides its own thinking; ``think`` is
ignored.

Overloads (HTTP 5xx) and timeouts are retried briefly. If they persist, and
for quota/rate limits (HTTP 429), a ``ProviderUnavailableError`` is raised so
the router can fall back to the unit's local model.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .env import env_value, load_local_env
from .llm import (
    LLMError, OutputLimitError, ProviderUnavailableError, RateLimitError, _positive_env, parse_json_object,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
MAX_OUTPUT_TOKENS = 16384
DEFAULT_TIMEOUT = 120.0
RETRY_DELAYS = (2.0, 5.0, 10.0)
_TYPES = {"object": "OBJECT", "array": "ARRAY", "string": "STRING", "integer": "INTEGER",
          "number": "NUMBER", "boolean": "BOOLEAN"}


def to_gemini_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Convert the agent's JSON Schemas (type/properties/required/items/enum) to Gemini's schema format."""
    out: dict[str, Any] = {}
    kind = schema.get("type")
    if isinstance(kind, str):
        out["type"] = _TYPES.get(kind, kind.upper())
    if "enum" in schema:
        out["enum"] = list(schema["enum"])
    if "properties" in schema:
        out["properties"] = {k: to_gemini_schema(v) for k, v in schema["properties"].items()}
        out["propertyOrdering"] = list(schema["properties"])
    if "required" in schema:
        out["required"] = list(schema["required"])
    if "items" in schema:
        out["items"] = to_gemini_schema(schema["items"])
    return out


class GeminiClient:
    def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None,
                 timeout: float | None = None, max_output_tokens: int = MAX_OUTPUT_TOKENS) -> None:
        load_local_env()
        self.model = model.removeprefix("models/")
        self.api_key = api_key or env_value("GEMINI_API_KEY")
        if not self.api_key:
            raise LLMError(f"GEMINI_API_KEY is not set (needed for gemini:{self.model}); add it to the ALISA .env")
        self.base_url = (base_url or env_value("GEMINI_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.timeout = float(timeout or _positive_env("GEMINI_TIMEOUT", DEFAULT_TIMEOUT, float))
        retries = env_value("GEMINI_RETRIES")
        self.retries = int(retries) if retries and retries.isdigit() else 2
        self.max_output_tokens = max_output_tokens
        self.sleep = time.sleep
        self.quota_key = f"gemini:{self.model}"
        self.last_usage: dict[str, int] = {}

    def unload(self) -> bool:
        return True  # nothing held locally

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/models/{self.model}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def generate_json(
        self, system: str, prompt: str, *, schema: Mapping[str, Any] | None = None,
        think: bool | None = None, temperature: float | None = None,
    ) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        config: dict[str, Any] = {"responseMimeType": "application/json", "maxOutputTokens": self.max_output_tokens}
        if schema is not None:
            config["responseSchema"] = to_gemini_schema(schema)
        if temperature is not None:
            config["temperature"] = temperature
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        }
        from .quota import limits_for, tracker

        self.last_usage = {}
        estimated = (len(system) + len(prompt)) // 4 + 1024
        tracker().before_call(self.quota_key, limits_for("gemini", self.model), estimated)
        started = time.monotonic()
        data = self._send(payload, started)
        usage = data.get("usageMetadata") or {}
        self.last_usage = {
            "prompt": int(usage.get("promptTokenCount") or 0),
            "output": int(usage.get("candidatesTokenCount") or 0),
            "thinking": int(usage.get("thoughtsTokenCount") or 0),
            "total": int(usage.get("totalTokenCount") or 0),
        }
        tracker().record(self.quota_key, self.last_usage["total"] or estimated)

        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise LLMError(f"Gemini blocked the prompt: {feedback['blockReason']}")
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("Gemini returned no candidates")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        reason = candidate.get("finishReason")
        if reason == "MAX_TOKENS":
            raise OutputLimitError(f"Gemini {self.model} hit its output limit ({self.max_output_tokens}) before finishing")
        if reason not in (None, "STOP") and not text.strip():
            raise LLMError(f"Gemini {self.model} stopped without an answer ({reason})")
        LOGGER.info("Gemini %s finished in %.1fs (prompt_chars=%d, response_chars=%d)",
                    self.model, time.monotonic() - started, len(prompt), len(text))
        return parse_json_object(text)

    def _send(self, payload: dict[str, Any], started: float) -> dict[str, Any]:
        """POST with brief retries on overload and timeouts."""
        attempt = 0
        while True:
            try:
                return self._post(payload)
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                detail = " ".join(body.split())[:300]
                if exc.code == 429:
                    from .quota import parse_rate_limit, tracker

                    delay, daily = parse_rate_limit(body)
                    tracker().bench(self.quota_key, delay, daily=daily)
                    scope = "daily quota spent" if daily else f"retry in {delay:.0f}s" if delay else "rate limited"
                    raise RateLimitError(f"Gemini {self.model} {scope}: {detail}") from exc
                if exc.code < 500:
                    raise LLMError(f"Gemini {self.model} returned HTTP {exc.code}: {detail}") from exc
                problem = f"HTTP {exc.code} {detail}"
            except (URLError, TimeoutError, OSError) as exc:
                problem = f"request failed ({exc})"
            if attempt >= self.retries:
                raise ProviderUnavailableError(
                    f"Gemini {self.model} unavailable after {attempt + 1} attempt(s) "
                    f"and {time.monotonic() - started:.0f}s: {problem}")
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            LOGGER.warning("Gemini %s: %s; retrying in %.0fs", self.model, problem, delay)
            self.sleep(delay)
            attempt += 1
