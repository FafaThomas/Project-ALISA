
"""Minimal Ollama client for the ALISA Coding Agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[5]


def load_local_env() -> None:
    """Load project .env values without overriding existing environment variables."""
    env_path = PROJECT_ROOT / ".env"

    if not env_path.is_file():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")

        if key:
            os.environ.setdefault(key, value)


class LLMError(RuntimeError):
    """Raised when an LLM request fails or returns an invalid response."""


class OllamaClient:
    """Send prompts to a locally running Ollama model."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        load_local_env()

        self.model = model or os.getenv("OLLAMA_MODEL")
        self.base_url = (
            base_url or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        ).rstrip("/")
        self.timeout = timeout

        if not self.model:
            raise ValueError(
                "Specify a model or set OLLAMA_MODEL in the project .env."
            )

    def generate_json(self, prompt: str) -> dict:
        """Generate a JSON object from a prompt."""
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are ALISA Forge, a software engineering agent. "
                        "Follow the task and constraints. Return one valid "
                        "JSON object without Markdown fences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
        }

        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(
                f"Ollama returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"Could not reach Ollama: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError("Ollama returned an invalid API response.") from exc

        content = result.get("message", {}).get("content")

        if not isinstance(content, str) or not content.strip():
            raise LLMError("Ollama returned an empty response.")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError("The model did not return valid JSON.") from exc

        if not isinstance(parsed, dict):
            raise LLMError("The model response must be a JSON object.")

        return parsed
