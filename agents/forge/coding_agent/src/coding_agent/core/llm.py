"""LLM access for the coding team.

Every specialist talks to an ``LLMClient``. Roles are served by model *tiers*
(planning, coding, review) so a local machine can run a strong planning model
and a code model one after the other. ``ModelRouter`` unloads the previous
model when the tier's model changes, to keep VRAM free on single-GPU hosts.

Environment (read from the process or the ALISA project ``.env``):

- ``OLLAMA_HOST``                      Ollama URL (default http://localhost:11434)
- ``PLANNING_OLLAMA_MODEL``            planning tier
- ``CODING_AGENT_OLLAMA_MODEL``        coding tier
- ``REVIEW_OLLAMA_MODEL``              review tier (falls back to planning)
- ``OLLAMA_MODEL``                     shared fallback for every tier
- ``CODING_AGENT_LLM_TIMEOUT``         seconds per request (default 300)
- ``CODING_AGENT_OLLAMA_NUM_PREDICT``  output token cap (default 8192)
- ``CODING_AGENT_OLLAMA_NUM_CTX``      context window (default 16384)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_NUM_PREDICT = 8192
DEFAULT_NUM_CTX = 16384

_TIER_ENV: dict[str, tuple[str, ...]] = {
    "planning": ("PLANNING_OLLAMA_MODEL", "OLLAMA_MODEL", "CODING_AGENT_OLLAMA_MODEL"),
    "coding": ("CODING_AGENT_OLLAMA_MODEL", "OLLAMA_MODEL"),
    "review": ("REVIEW_OLLAMA_MODEL", "PLANNING_OLLAMA_MODEL", "OLLAMA_MODEL", "CODING_AGENT_OLLAMA_MODEL"),
}


class LLMError(RuntimeError):
    """An LLM request failed or returned something unusable."""


class LLMClient(Protocol):
    model: str

    def generate_json(
        self, system: str, prompt: str, *, schema: Mapping[str, Any] | None = None
    ) -> dict[str, Any]: ...

    def unload(self) -> bool: ...


def _find_project_env() -> Path | None:
    """Locate the ALISA project .env by walking up from this file."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "README.MD").is_file() and (parent / "agents").is_dir():
            env = parent / ".env"
            return env if env.is_file() else None
    return None


def load_local_env() -> None:
    """Load project .env values without overriding the existing environment."""
    env_path = _find_project_env()
    if env_path is None:
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("'\""))


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
        value = os.getenv(name)
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
        self, system: str, prompt: str, *, schema: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
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
        if final.get("done_reason") == "length":
            raise LLMError(
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
    """Hands out one client per tier and unloads models when the tier changes."""

    def __init__(self, clients: Mapping[str, LLMClient] | None = None, *, unload_on_switch: bool = True) -> None:
        self._clients: dict[str, LLMClient] = dict(clients or {})
        self._active: LLMClient | None = None
        self.unload_on_switch = unload_on_switch

    @classmethod
    def single(cls, client: LLMClient) -> "ModelRouter":
        """Serve every tier with one client (tests, hosted models)."""
        return cls({tier: client for tier in _TIER_ENV}, unload_on_switch=False)

    def client_for(self, tier: str) -> LLMClient:
        client = self._clients.get(tier)
        if client is None:
            model = model_for_tier(tier)
            if not model:
                raise LLMError(
                    f"No model configured for the {tier} tier. Set {_TIER_ENV[tier][0]} or OLLAMA_MODEL."
                )
            reuse = next((c for c in self._clients.values() if c.model == model), None)
            client = reuse or OllamaClient(model)
            self._clients[tier] = client
        if (
            self.unload_on_switch
            and self._active is not None
            and self._active is not client
            and self._active.model != client.model
        ):
            self._active.unload()
        self._active = client
        return client
