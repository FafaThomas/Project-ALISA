"""OpenAI-compatible Chat Completions client for units whose model is ``openai:<model>``.

    FORGE_OPENAI_REASONING_MODEL=<a model id your key can use>
    FORGE_CODING_AGENT_QUALITY_DEBUGGING_SPECIALIST_MODEL=openai:@reasoning

Works with OpenAI and with any server that speaks its Chat Completions API: point
``OPENAI_API_BASE`` at OpenRouter (https://openrouter.ai/api/v1), DeepSeek, Groq, or a
local LM Studio or vLLM server.

Environment:

- ``OPENAI_API_KEY``            the key; servers that need none (local ones) work without it
- ``OPENAI_API_BASE``           default https://api.openai.com/v1
- ``OPENAI_TIMEOUT``            seconds per request (default 300: reasoning models think first)
- ``OPENAI_RETRIES``            retries on overload, timeouts and short rate limits (default 2)
- ``OPENAI_REASONING_EFFORT``   optional, e.g. ``low``/``medium``/``high``; sent as reasoning_effort
- ``OPENAI_MAX_OUTPUT_TOKENS``  optional output cap; unset leaves the model's own limit
- ``OPENAI_QUOTA_<MODEL>``      optional rpm,tpm,rpd limits for quota-aware routing (core/quota.py)

Replies follow the agent's JSON Schema through ``response_format`` (json_schema, not strict:
strict mode needs every field required, and the agent's schemas have optional ones). Models
and servers differ in what they accept, so the client adapts once and remembers it:

- a parameter the model refuses (``temperature`` on reasoning models, ``reasoning_effort`` on
  others) is dropped;
- no json_schema support: json_object, with the schema written into the system message;
- no response_format at all: plain text, parsed as JSON.

Overloads, timeouts and short rate limits are retried. A missing key, a rejected key, an
unknown model, a spent quota or a persistent failure raise ``ProviderUnavailableError``, so
the router falls back to the unit's local model and the report says why.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .env import env_value, load_local_env
from .llm import LLMError, OutputLimitError, ProviderUnavailableError, RateLimitError, _positive_env, parse_json_object

LOGGER = logging.getLogger(__name__)
DEFAULT_BASE = "https://api.openai.com/v1"
DEFAULT_TIMEOUT = 300.0
RETRY_DELAYS = (2.0, 5.0, 10.0)
MAX_RATE_LIMIT_WAIT = 20.0  # a 429 asking to wait longer than this falls back to the local model instead
DROPPABLE = ("temperature", "reasoning_effort", "max_completion_tokens")


def _duration(text: str) -> float | None:
    """Seconds from '20', '1.5s', '6m0s' or '250ms' (OpenAI's rate-limit headers), or None."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    total, found = 0.0, False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", text):
        found = True
        total += float(amount) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else None


class OpenAICompatClient:
    def __init__(self, model: str, *, api_key: str | None = None, base_url: str | None = None,
                 timeout: float | None = None) -> None:
        load_local_env()
        self.model = model
        self.api_key = api_key or env_value("OPENAI_API_KEY") or ""
        self.base_url = (base_url or env_value("OPENAI_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.timeout = float(timeout or _positive_env("OPENAI_TIMEOUT", DEFAULT_TIMEOUT, float))
        retries = env_value("OPENAI_RETRIES")
        self.retries = int(retries) if retries and retries.isdigit() else 2
        self.reasoning_effort = (env_value("OPENAI_REASONING_EFFORT") or "").strip()
        cap = env_value("OPENAI_MAX_OUTPUT_TOKENS")
        self.max_output_tokens = int(cap) if cap and cap.isdigit() and int(cap) > 0 else None
        self.schema_mode = "json_schema"  # then "json_object", then "text"
        self.dropped: set[str] = set()
        self.sleep = time.sleep
        self.quota_key = f"openai:{self.model}"
        self.last_usage: dict[str, int] = {}

    def unload(self) -> bool:
        return True  # nothing held locally

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(f"{self.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"),
                          headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    # ----------------------------------------------------------------- request
    def _payload(self, system: str, prompt: str, schema: Mapping[str, Any] | None,
                 temperature: float | None) -> dict[str, Any]:
        if self.schema_mode == "json_schema" and schema is not None:
            response_format = {"type": "json_schema",
                               "json_schema": {"name": "reply", "schema": dict(schema), "strict": False}}
        else:
            # json_object (OpenAI wants the word JSON in the messages for it), or plain text.
            response_format = {"type": "json_object"} if self.schema_mode != "text" else None
            system += ("\n\nReply with one JSON object that matches this JSON Schema, and nothing else:\n"
                       f"{json.dumps(schema)}" if schema is not None else
                       "\n\nReply with one JSON object, and nothing else.")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        if response_format:
            payload["response_format"] = response_format
        optional = {"temperature": temperature, "reasoning_effort": self.reasoning_effort or None,
                    "max_completion_tokens": self.max_output_tokens}
        payload.update({k: v for k, v in optional.items() if v is not None and k not in self.dropped})
        return payload

    def _adapt(self, error: dict[str, Any], payload: dict[str, Any]) -> bool:
        """After HTTP 400: drop what the model or server refused. True when there is something to retry."""
        param = str(error.get("param") or "")
        message = str(error.get("message") or "")
        for name in DROPPABLE:
            if name in payload and (param == name or name in message):
                self.dropped.add(name)
                LOGGER.info("openai:%s does not accept %s; sending without it", self.model, name)
                return True
        if "response_format" in payload and (param.startswith("response_format") or "response_format" in message
                                             or "json_schema" in message or "json_object" in message):
            self.schema_mode = "json_object" if self.schema_mode == "json_schema" else "text"
            LOGGER.info("openai:%s: structured output not supported as sent; using %s", self.model, self.schema_mode)
            return True
        return False

    # ------------------------------------------------------------------- reply
    def generate_json(
        self, system: str, prompt: str, *, schema: Mapping[str, Any] | None = None,
        think: bool | None = None, temperature: float | None = None,
    ) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if not self.api_key and self.base_url == DEFAULT_BASE:
            raise ProviderUnavailableError(
                f"OPENAI_API_KEY is not set (needed for openai:{self.model}); add it to the ALISA .env")
        from .quota import limits_for, tracker

        self.last_usage = {}
        estimated = (len(system) + len(prompt)) // 4 + 1024
        tracker().before_call(self.quota_key, limits_for("openai", self.model), estimated)
        started = time.monotonic()
        data = self._send(lambda: self._payload(system, prompt, schema, temperature), started)
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        self.last_usage = {
            "prompt": int(usage.get("prompt_tokens") or 0),
            "output": int(usage.get("completion_tokens") or 0),
            "thinking": int(details.get("reasoning_tokens") or 0),
            "total": int(usage.get("total_tokens") or 0),
        }
        tracker().record(self.quota_key, self.last_usage["total"] or estimated)

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"openai:{self.model} returned no choices")
        message = choices[0].get("message") or {}
        if message.get("refusal"):
            raise LLMError(f"openai:{self.model} refused: {str(message['refusal'])[:200]}")
        text = message.get("content") or ""
        if choices[0].get("finish_reason") == "length":
            raise OutputLimitError(f"openai:{self.model} hit its output limit before the JSON finished")
        if not text.strip():
            raise LLMError(f"openai:{self.model} returned an empty answer ({choices[0].get('finish_reason')})")
        LOGGER.info("openai:%s finished in %.1fs (prompt_chars=%d, response_chars=%d)",
                    self.model, time.monotonic() - started, len(prompt), len(text))
        return parse_json_object(text)

    def _send(self, build, started: float) -> dict[str, Any]:
        """POST, adapting to refused parameters and retrying overloads, timeouts and short rate limits."""
        attempt, adaptations = 0, 0
        while True:
            payload = build()
            try:
                return self._post(payload)
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                detail = " ".join(body.split())[:300]
                try:
                    error = (json.loads(body) or {}).get("error") or {}
                except ValueError:
                    error = {}
                error = error if isinstance(error, dict) else {"message": str(error)}
                if exc.code == 400 and adaptations < 4 and self._adapt(error, payload):
                    adaptations += 1
                    continue
                if exc.code == 429:
                    wait = self._rate_limited(exc, error, detail)
                    if wait is not None and attempt < self.retries:
                        self.sleep(wait)
                        attempt += 1
                        continue
                    raise RateLimitError(f"openai:{self.model} rate limited: {detail}") from exc
                if exc.code in (401, 403, 404):
                    # A rejected key or a model this key can't use: the unit's local model answers instead.
                    raise ProviderUnavailableError(f"openai:{self.model} returned HTTP {exc.code}: {detail}") from exc
                if exc.code < 500:
                    raise LLMError(f"openai:{self.model} returned HTTP {exc.code}: {detail}") from exc
                problem = f"HTTP {exc.code} {detail}"
            except (URLError, TimeoutError, OSError) as exc:
                problem = f"request failed ({exc})"
            if attempt >= self.retries:
                raise ProviderUnavailableError(
                    f"openai:{self.model} unavailable after {attempt + 1} attempt(s) "
                    f"and {time.monotonic() - started:.0f}s: {problem}")
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            LOGGER.warning("openai:%s: %s; retrying in %.0fs", self.model, problem, delay)
            self.sleep(delay)
            attempt += 1

    def _rate_limited(self, exc: HTTPError, error: dict[str, Any], detail: str) -> float | None:
        """Seconds to wait before retrying a 429, or None to fall back now (and bench the model)."""
        from .quota import tracker

        if error.get("code") == "insufficient_quota" or error.get("type") == "insufficient_quota":
            tracker().bench(self.quota_key, 3600.0, daily=True)  # billing: not something waiting fixes
            return None
        headers = exc.headers or {}
        wait = next((d for d in (_duration(headers.get(h, "")) for h in (
            "retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")) if d is not None), None)
        if wait is not None and wait <= MAX_RATE_LIMIT_WAIT:
            return max(wait, 0.5)
        tracker().bench(self.quota_key, wait)
        LOGGER.warning("openai:%s rate limited (%s); benched", self.model, detail)
        return None
