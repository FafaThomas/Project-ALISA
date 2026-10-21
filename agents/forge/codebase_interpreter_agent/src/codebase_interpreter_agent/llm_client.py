
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _load_project_env() -> None:
    """Load missing environment settings from the ALISA project .env."""
    project_root = Path(__file__).resolve().parents[5]
    env_file = project_root / ".env"

    if not env_file.is_file():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and value[0:1] in {"'", '"'}:
            if value[-1:] == value[0]:
                value = value[1:-1]

        if key:
            os.environ.setdefault(key, value)


class OllamaError(RuntimeError):
    """Raised when Ollama cannot provide a valid response."""


class OllamaClient:
    """Small standard-library client for Ollama's chat API."""

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        _load_project_env()

        self.host = (
            host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        ).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
        self.timeout = timeout

        if not self.model.strip():
            raise ValueError("OLLAMA_MODEL cannot be empty.")

        if self.timeout <= 0:
            raise ValueError("timeout must be positive.")

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Return the assistant's response text from Ollama."""
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        request = Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(
                f"Ollama returned HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise OllamaError(
                f"Cannot connect to Ollama at {self.host}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise OllamaError(
                f"Ollama request timed out after {self.timeout:g} seconds."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OllamaError(
                "Ollama returned an invalid JSON response."
            ) from exc

        message = result.get("message")
        content = message.get("content") if isinstance(message, dict) else None

        if not isinstance(content, str) or not content.strip():
            raise OllamaError(
                "Ollama returned no assistant message content."
            )

        return content.strip()
