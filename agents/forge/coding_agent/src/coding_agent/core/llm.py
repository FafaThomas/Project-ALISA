"""LLM access for the coding team.

Every specialist talks to an ``LLMClient``. Roles are served by model *tiers*
(planning, coding, review) so a local machine can run a strong planning model
and a code model one after the other. ``ModelRouter`` unloads the previous
model when the tier's model changes, to keep VRAM free on single-GPU hosts.

Environment (read from the process or the ALISA project ``.env``):

- ``OLLAMA_HOST``                      Ollama URL (default http://localhost:11434)
- ``FORGE_CODING_AGENT_PLANNING_MODEL`` planning tier  (older name: PLANNING_OLLAMA_MODEL)
- ``FORGE_CODING_AGENT_CODING_MODEL``   coding tier    (older name: CODING_AGENT_OLLAMA_MODEL)
- ``FORGE_CODING_AGENT_REVIEW_MODEL``   review tier, falls back to planning (older: REVIEW_OLLAMA_MODEL)
- ``OLLAMA_MODEL``                     shared fallback for every tier
- ``CODING_AGENT_LLM_TIMEOUT``         seconds per request (default 300)
- ``CODING_AGENT_OLLAMA_NUM_PREDICT``  output token cap (default 8192)
- ``CODING_AGENT_OLLAMA_NUM_CTX``      context window (default 16384)

Models are looked up from the unit upward (unit, its team, its division, the
agent). At each level the ``.env`` variable named after the unit's place in
the hierarchy is checked first, then ``model:`` in its ``unit.md``:

    FORGE_CODING_AGENT_MODEL                                 the whole agent
    FORGE_CODING_AGENT_DEVELOPMENT_MODEL                     a division
    FORGE_CODING_AGENT_DEVELOPMENT_PYTHON_DEVELOPER_MODEL    a specialist

The first one found wins. Units with none use the tier defaults above.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .env import env_value, load_local_env

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_NUM_PREDICT = 8192
DEFAULT_NUM_CTX = 16384

# Tier defaults, most preferred first. The FORGE_CODING_AGENT_* names are the
# organized .env layout; the older names are still read as fallbacks.
_TIER_ENV: dict[str, tuple[str, ...]] = {
    "planning": ("FORGE_CODING_AGENT_PLANNING_MODEL", "PLANNING_OLLAMA_MODEL", "OLLAMA_MODEL"),
    "coding": ("FORGE_CODING_AGENT_CODING_MODEL", "CODING_AGENT_OLLAMA_MODEL", "OLLAMA_MODEL"),
    "review": ("FORGE_CODING_AGENT_REVIEW_MODEL", "REVIEW_OLLAMA_MODEL",
               "FORGE_CODING_AGENT_PLANNING_MODEL", "PLANNING_OLLAMA_MODEL", "OLLAMA_MODEL"),
}


class LLMError(RuntimeError):
    """An LLM request failed or returned something unusable."""


class OutputLimitError(LLMError):
    """The model ran out of output tokens before finishing its answer."""


class ProviderUnavailableError(LLMError):
    """A hosted provider could not be reached or is temporarily failing."""


class RateLimitError(ProviderUnavailableError):
    """A hosted provider refused the call for quota or rate-limit reasons."""


REMOTE_PREFIXES = ("gemini:",)


def split_provider(model: str) -> tuple[str, str]:
    """('gemini', 'gemini-2.5-flash') for 'gemini:gemini-2.5-flash'; ('ollama', model) otherwise."""
    for prefix in REMOTE_PREFIXES + ("ollama:",):
        if model.startswith(prefix):
            return prefix[:-1], model[len(prefix):]
    return "ollama", model


def resolve_alias(model: str) -> tuple[str, str]:
    """'gemini:@reasoning' -> ('gemini:<FORGE_GEMINI_REASONING_MODEL>', '.env FORGE_GEMINI_REASONING_MODEL')."""
    provider, name = split_provider(model)
    if not name.startswith("@"):
        return model, ""
    var = f"FORGE_{provider.upper()}_{name[1:].upper()}_MODEL"
    load_local_env()
    value = env_value(var)
    if not value:
        raise LLMError(f"{model} refers to {var}, which is not set in .env")
    return f"{provider}:{value}", f".env {var}"


def hosted_fallback_model(provider: str) -> str | None:
    """``FORGE_GEMINI_FALLBACK_MODEL``: another hosted model to try before the local one, or None."""
    load_local_env()
    value = env_value(f"FORGE_{provider.upper()}_FALLBACK_MODEL")
    if not value:
        return None
    if ":" not in value:
        value = f"{provider}:{value}"
    try:
        resolved, _ = resolve_alias(value)
    except LLMError as exc:
        LOGGER.warning("ignoring FORGE_%s_FALLBACK_MODEL: %s", provider.upper(), exc)
        return None
    return resolved if split_provider(resolved)[0] == provider else None


def default_client_factory(model: str) -> "LLMClient":
    provider, name = split_provider(model)
    if provider == "gemini":
        from .gemini import GeminiClient

        return GeminiClient(name)
    return OllamaClient(name)


class FallbackClient:
    """A hosted model that falls back to a local one when the provider is unavailable.

    After each call, ``used_model`` and ``fallback_reason`` say what actually answered.
    """

    def __init__(self, primary: "LLMClient", fallback: Callable[[], "LLMClient"], label: str,
                 kind: str = "local") -> None:
        self.primary = primary
        self._fallback = fallback
        self.model = label
        self.kind = kind                 # what the fallback is: "local" or "hosted"
        self.used_model = label
        self.fallback_reason = ""
        self.fallback_kind = ""          # set when a fallback answered
        self.last_usage: dict[str, int] = {}

    def unload(self) -> bool:
        return self.primary.unload()

    def generate_json(self, system: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        self.used_model, self.fallback_reason, self.fallback_kind = self.model, "", ""
        try:
            reply = self.primary.generate_json(system, prompt, **kwargs)
            self.last_usage = dict(getattr(self.primary, "last_usage", {}) or {})
            # The primary may itself be a fallback chain (hosted -> second hosted model).
            self.used_model = getattr(self.primary, "used_model", None) or self.model
            self.fallback_reason = getattr(self.primary, "fallback_reason", "") or ""
            self.fallback_kind = getattr(self.primary, "fallback_kind", "") or ""
            return reply
        except ProviderUnavailableError as exc:
            local = self._fallback()
            LOGGER.warning("%s unavailable (%s); falling back to %s", self.model, exc, local.model)
            self.used_model, self.fallback_reason, self.fallback_kind = local.model, str(exc)[:200], self.kind
            reply = local.generate_json(system, prompt, **kwargs)
            self.last_usage = dict(getattr(local, "last_usage", {}) or {})
            return reply


class LLMClient(Protocol):
    model: str

    def generate_json(
        self, system: str, prompt: str, *, schema: Mapping[str, Any] | None = None,
        think: bool | None = None, temperature: float | None = None,
    ) -> dict[str, Any]: ...

    def unload(self) -> bool: ...




def _positive_env(name: str, default: float, cast: type) -> Any:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def model_for_tier(tier: str) -> str | None:
    load_local_env()
    for name in _TIER_ENV.get(tier, ("OLLAMA_MODEL",)):
        value = env_value(name)
        if value:
            return value
    return None


class OllamaClient:
    """Structured-JSON chat against a local Ollama server (stdlib only)."""

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        timeout: float | None = None,
        num_predict: int | None = None,
        num_ctx: int | None = None,
    ) -> None:
        load_local_env()
        if not model:
            raise ValueError("model must be set")
        self.model = model
        self.base_url = (base_url or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.timeout = float(timeout or _positive_env("CODING_AGENT_LLM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS, float))
        self.num_predict = int(num_predict or _positive_env("CODING_AGENT_OLLAMA_NUM_PREDICT", DEFAULT_NUM_PREDICT, int))
        self.num_ctx = int(num_ctx or _positive_env("CODING_AGENT_OLLAMA_NUM_CTX", DEFAULT_NUM_CTX, int))
        self.last_usage: dict[str, int] = {}

    def _post(self, path: str, payload: dict[str, Any], timeout: float):
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urlopen(request, timeout=timeout)

    def unload(self) -> bool:
        try:
            with self._post("/api/generate", {"model": self.model, "keep_alive": 0}, min(self.timeout, 30.0)) as r:
                r.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            LOGGER.warning("Could not unload Ollama model %s: %s", self.model, exc)
            return False
        return True

    def generate_json(
        self, system: str, prompt: str, *, schema: Mapping[str, Any] | None = None,
        think: bool | None = None, temperature: float | None = None,
    ) -> dict[str, Any]:
        """One structured reply.

        ``think``: False stops thinking models (qwen3, deepseek-r1, ...) from
        reasoning before they answer, which is slower and can use up the
        output budget before the JSON is written. None leaves Ollama's default.
        ``temperature``: sampling temperature; None leaves Ollama's default.
        """
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "format": dict(schema) if schema is not None else "json",
            "stream": True,
            "options": {"num_predict": self.num_predict, "num_ctx": self.num_ctx},
        }
        if think is not None:
            payload["think"] = think
        if temperature is not None:
            payload["options"]["temperature"] = temperature
        started = time.monotonic()
        parts: list[str] = []
        final: dict[str, Any] = {}
        try:
            with self._post("/api/chat", payload, self.timeout) as response:
                for raw in response:
                    if not raw.strip():
                        continue
                    try:
                        record = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise LLMError("Ollama returned an invalid streaming record") from exc
                    chunk = (record.get("message") or {}).get("content", "")
                    if chunk:
                        parts.append(chunk)
                    if record.get("done") is True:
                        final = record
                        break
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"Ollama request failed after {time.monotonic() - started:.1f}s: {exc}") from exc

        LOGGER.info(
            "Ollama %s finished in %.1fs (prompt_chars=%d, response_chars=%d)",
            self.model, time.monotonic() - started, len(prompt), sum(map(len, parts)),
        )
        if not final:
            raise LLMError("Ollama closed the stream without a final record")
        prompt_tokens, output_tokens = int(final.get("prompt_eval_count") or 0), int(final.get("eval_count") or 0)
        self.last_usage = {"prompt": prompt_tokens, "output": output_tokens, "total": prompt_tokens + output_tokens}
        if final.get("done_reason") == "length":
            raise OutputLimitError(
                f"Output hit CODING_AGENT_OLLAMA_NUM_PREDICT ({self.num_predict}) before the JSON finished"
            )
        return parse_json_object("".join(parts))


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a model reply as one JSON object, tolerating Markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("The model did not return a JSON object") from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("The model did not return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMError("The model response must be a JSON object")
    return parsed


class ModelRouter:
    """Picks the client for each call.

    A unit's model comes from its ``unit.md`` (or the nearest unit above it
    that declares one). Units without one use their tier's model from the
    ``.env``. ``resolve`` returns the client together with a note saying where
    the choice came from, which reports record for every specialist.

    Switching to a different model unloads the previous one, to keep VRAM
    free on single-GPU hosts.
    """

    def __init__(
        self,
        clients: Mapping[str, LLMClient] | None = None,
        *,
        unload_on_switch: bool = True,
        client_factory: Callable[[str], LLMClient] | None = None,
    ) -> None:
        self._clients: dict[str, LLMClient] = dict(clients or {})   # by tier
        self._by_model: dict[str, LLMClient] = {c.model: c for c in self._clients.values()}
        self._fixed: LLMClient | None = None
        self._env_source: dict[str, str] = {}
        self._active: LLMClient | None = None
        self.unload_on_switch = unload_on_switch
        self.client_factory = client_factory or default_client_factory

    @classmethod
    def single(cls, client: LLMClient) -> "ModelRouter":
        """Serve every unit with one client (tests, or one hosted model)."""
        router = cls(unload_on_switch=False)
        router._fixed = client
        return router

    def _model_client(self, model: str) -> LLMClient:
        client = self._by_model.get(model)
        if client is None:
            client = self._by_model[model] = self.client_factory(model)
        return client

    def _tier_client(self, tier: str) -> LLMClient:
        """The tier's default (local) client, without touching unload bookkeeping."""
        client = self._clients.get(tier)
        if client is None:
            load_local_env()
            env_name = next((n for n in _TIER_ENV.get(tier, ()) if env_value(n)), None)
            if env_name is None:
                raise LLMError(
                    f"No model configured for the {tier} tier. Set {_TIER_ENV[tier][0]} or OLLAMA_MODEL, "
                    "or give the unit a model in .env or its unit.md."
                )
            client = self._clients[tier] = self._model_client(env_value(env_name))  # type: ignore[arg-type]
            self._env_source[tier] = f".env {env_name}"
        return client

    def resolve(
        self, tier: str, model: str | None = None, source: str | None = None,
    ) -> tuple[LLMClient, str]:
        """(client, source). ``model``/``source`` come from ``Tree.effective_model`` when the
        unit (or a unit above it) has its own model; otherwise the tier default is used."""
        if self._fixed is not None:
            return self._fixed, "fixed client"
        if model:
            source = source or "unit model"
            model, alias_source = resolve_alias(model)
            if alias_source:
                source = f"{source} -> {alias_source}"
            provider = split_provider(model)[0]
            if provider != "ollama":
                hosted_fallback = hosted_fallback_model(provider)
                if hosted_fallback == model:
                    hosted_fallback = None
                key = f"{model}|fallback:{hosted_fallback or ''}|{tier}"
                client = self._by_model.get(key)
                if client is None:
                    primary: LLMClient = self._model_client(model)
                    if hosted_fallback:
                        # e.g. gemini-2.5-flash out of quota -> gemini-3.5-flash-lite -> the local tier model
                        primary = FallbackClient(primary, lambda fb=hosted_fallback: self._model_client(fb), model,
                                                 kind="hosted")
                    client = self._by_model[key] = FallbackClient(primary, lambda: self._tier_client(tier), model)
            else:
                client = self._model_client(model)
        else:
            client = self._tier_client(tier)
            source = self._env_source.get(tier, f"{tier} tier client")
        if (
            self.unload_on_switch
            and self._active is not None
            and self._active is not client
            and self._active.model != client.model
        ):
            self._active.unload()
        self._active = client
        return client, source

    def client_for(self, tier: str) -> LLMClient:
        return self.resolve(tier)[0]
