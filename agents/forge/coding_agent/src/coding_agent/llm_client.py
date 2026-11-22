"""Minimal Ollama client for the ALISA Coding Agent."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_NUM_PREDICT = 4096
PROGRESS_LOG_INTERVAL_SECONDS = 30.0
LOGGER = logging.getLogger(__name__)


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
        timeout: float | None = None,
        num_predict: int | None = None,
    ) -> None:
        load_local_env()

        # Keep the Coding Agent's model independently configurable so changing
        # this agent does not silently change other ALISA components.
        self.model = (
            model
            or os.getenv("CODING_AGENT_OLLAMA_MODEL")
            or os.getenv("OLLAMA_MODEL")
        )
        self.base_url = (
            base_url or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        ).rstrip("/")

        timeout_from_environment = timeout is None
        if timeout_from_environment:
            configured_timeout = os.getenv("CODING_AGENT_LLM_TIMEOUT")
            try:
                timeout = (
                    float(configured_timeout)
                    if configured_timeout is not None
                    else DEFAULT_TIMEOUT_SECONDS
                )
            except ValueError as exc:
                raise ValueError(
                    "CODING_AGENT_LLM_TIMEOUT must be a positive number of seconds."
                ) from exc

        if timeout <= 0:
            setting = (
                "CODING_AGENT_LLM_TIMEOUT"
                if timeout_from_environment
                else "LLM timeout"
            )
            raise ValueError(f"{setting} must be greater than zero seconds.")

        self.timeout = float(timeout)

        if num_predict is None:
            configured_num_predict = os.getenv("CODING_AGENT_OLLAMA_NUM_PREDICT")
            try:
                num_predict = (
                    int(configured_num_predict)
                    if configured_num_predict is not None
                    else DEFAULT_NUM_PREDICT
                )
            except ValueError as exc:
                raise ValueError(
                    "CODING_AGENT_OLLAMA_NUM_PREDICT must be a positive integer."
                ) from exc
        if isinstance(num_predict, bool) or not isinstance(num_predict, int) or num_predict < 1:
            raise ValueError("num_predict must be a positive integer.")
        self.num_predict = num_predict
        self.last_request_duration_seconds: float | None = None

        if not self.model:
            raise ValueError(
                "Specify a model or set CODING_AGENT_OLLAMA_MODEL "
                "(or the shared OLLAMA_MODEL fallback) in the project .env."
            )

    def generate_json(
        self, prompt: str, *, schema: Mapping[str, object] | None = None
    ) -> dict:
        """Generate a JSON object, optionally constrained by a JSON Schema.

        Ollama accepts either ``"json"`` or a JSON Schema object in the
        ``format`` field. Schema constraints shape the response; callers must
        still perform semantic validation after parsing.
        """
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
            "format": dict(schema) if schema is not None else "json",
            # Stream token chunks so a long generation does not appear frozen
            # until Ollama has finished constructing the entire JSON response.
            "stream": True,
            "options": {"num_predict": self.num_predict},
        }

        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.monotonic()
        self.last_request_duration_seconds = None
        LOGGER.info(
            "Ollama request started (model=%s, prompt_chars=%d, num_predict=%d, timeout=%.1fs)",
            self.model,
            len(prompt),
            self.num_predict,
            self.timeout,
        )
        content_parts: list[str] = []
        final_record: dict = {}
        last_progress_log = started
        first_chunk_logged = False
        try:
            with urlopen(request, timeout=self.timeout) as response:
                while True:
                    raw_line = response.readline()
                    if not raw_line:
                        break
                    if not raw_line.strip():
                        continue
                    try:
                        record = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        elapsed = self._record_duration(started)
                        raise LLMError(
                            f"Ollama returned an invalid streaming record after {elapsed:.2f}s."
                        ) from exc
                    if not isinstance(record, dict):
                        raise LLMError("Ollama returned a non-object streaming record.")

                    message = record.get("message") or {}
                    chunk = message.get("content", "")
                    if chunk:
                        if not isinstance(chunk, str):
                            raise LLMError("Ollama returned a non-text response chunk.")
                        content_parts.append(chunk)
                        now = time.monotonic()
                        if not first_chunk_logged:
                            LOGGER.info(
                                "Ollama began streaming response (model=%s, prompt_chars=%d)",
                                self.model,
                                len(prompt),
                            )
                            first_chunk_logged = True
                        elif now - last_progress_log >= PROGRESS_LOG_INTERVAL_SECONDS:
                            LOGGER.info(
                                "Ollama still generating (model=%s, elapsed=%.1fs, response_chars=%d)",
                                self.model,
                                now - started,
                                sum(map(len, content_parts)),
                            )
                            last_progress_log = now

                    if record.get("done") is True:
                        final_record = record
                        break
        except HTTPError as exc:
            elapsed = self._record_duration(started)
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(
                f"Ollama returned HTTP {exc.code} after {elapsed:.2f}s: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            elapsed = self._record_duration(started)
            reason = exc.reason if isinstance(exc, URLError) else exc
            if isinstance(reason, TimeoutError):
                raise LLMError(
                    "Ollama request timed out after "
                    f"{elapsed:.2f}s without receiving a response chunk "
                    f"(socket timeout: {self.timeout:.1f}s; "
                    f"generated_chars_so_far={sum(map(len, content_parts))}). "
                    "Streaming progress is recorded above when tokens arrive. "
                    "The model may still be processing the request."
                ) from exc
            raise LLMError(
                f"Could not connect to Ollama after {elapsed:.2f}s: {exc}"
            ) from exc

        elapsed = self._record_duration(started)
        response_content = "".join(content_parts)
        LOGGER.info(
            "Ollama request completed in %.2fs (model=%s, timeout=%.1fs, "
            "prompt_chars=%d, response_chars=%d, eval_count=%s, done_reason=%s)",
            elapsed,
            self.model,
            self.timeout,
            len(prompt),
            len(response_content),
            final_record.get("eval_count", "unknown"),
            final_record.get("done_reason", "unknown"),
        )

        if not final_record:
            raise LLMError(
                f"Ollama closed the streaming response without a final record "
                f"after {elapsed:.2f}s."
            )
        if final_record.get("done_reason") == "length":
            raise LLMError(
                "Ollama reached CODING_AGENT_OLLAMA_NUM_PREDICT "
                f"({self.num_predict}) before completing the JSON response. "
                "Increase the output-token limit or reduce the amount of work "
                "requested in a single generation."
            )
        if not response_content.strip():
            raise LLMError(
                f"Ollama returned an empty response after {elapsed:.2f}s."
            )

        try:
            parsed = json.loads(response_content)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"The model did not return valid JSON after {elapsed:.2f}s."
            ) from exc

        if not isinstance(parsed, dict):
            raise LLMError("The model response must be a JSON object.")

        return parsed

    def _record_duration(self, started: float) -> float:
        """Store and return elapsed wall-clock time for the latest request."""
        elapsed = time.monotonic() - started
        self.last_request_duration_seconds = elapsed
        return elapsed
