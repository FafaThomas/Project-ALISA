"""HTTP API for invoking the coding agent directly (standard library only).

Endpoints
---------
GET  /health                         liveness
GET  /units                          the hierarchy: agent > divisions > specialists
POST /runs                           run the whole agent
POST /divisions/<division_id>/runs   run one division
POST /specialists/<specialist>/runs  run one specialist
GET  /runs/<job_id>                  job status, progress events and (when done) report

POST bodies are CodingRequest payloads. POSTs return 202 with a job id; add
?wait=true to block until the run finishes.

API invocations never get the Forge RAG pipeline. Workspaces must sit under
one of the configured allowed roots, and a bearer token is required when
``token`` is set (or ``CODING_AGENT_API_TOKEN`` is in the environment).
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent import CodingAgent
from .core.models import CodingRequest, InvocationContext, InvocationSource

LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 256_000


@dataclass
class Job:
    id: str
    status: str = "queued"
    events: list[dict] = field(default_factory=list)
    report: dict | None = None
    error: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {"job_id": self.id, "status": self.status, "events": list(self.events),
                "report": self.report, "error": self.error}


class CodingAgentService:
    """Transport-independent request handling; the HTTP layer is a thin shell."""

    def __init__(self, agent: CodingAgent, allowed_roots: list[str | Path], token: str | None = None) -> None:
        if not allowed_roots:
            raise ValueError("at least one allowed workspace root is required")
        self.agent = agent
        self.allowed_roots = [Path(r).expanduser().resolve() for r in allowed_roots]
        self.token = token if token is not None else os.getenv("CODING_AGENT_API_TOKEN") or None
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def authorized(self, header: str | None) -> bool:
        if not self.token:
            return True
        expected = f"Bearer {self.token}"
        return bool(header) and hmac.compare_digest(header.encode(), expected.encode())

    def validate(self, payload: dict[str, Any]) -> CodingRequest:
        request = CodingRequest.from_payload(payload)
        workspace = Path(request.workspace).expanduser().resolve()
        if not any(workspace == root or root in workspace.parents for root in self.allowed_roots):
            raise PermissionError("workspace is outside the allowed roots")
        request.workspace = str(workspace)
        return request

    def start(self, request: CodingRequest, *, level: str = "agent", unit_id: str | None = None,
              wait: bool = False) -> Job:
        unit = self.agent.unit(level, unit_id)  # validates before a job exists
        job = Job(id=uuid.uuid4().hex[:12])
        with self._lock:
            self.jobs[job.id] = job

        def emit(event: dict) -> None:
            with self._lock:
                job.events.append(event)

        def work() -> None:
            job.status = "running"
            invocation = InvocationContext(source=InvocationSource.API, caller_id="api")
            try:
                report = unit.run(request, invocation, emit=emit)
                job.report = report.to_dict()
                job.status = report.status
                job.error = report.error
            except Exception as exc:  # pragma: no cover - run() already reports failures
                job.status, job.error = "failed", str(exc)

        if wait:
            work()
        else:
            threading.Thread(target=work, name=f"coding-agent-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def units(self) -> dict[str, Any]:
        return self.agent.describe()


def make_handler(service: CodingAgentService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ALISA-CodingAgent/0.2"

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.info("api %s - " + fmt, self.address_string(), *args)

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _guard(self) -> bool:
            if service.authorized(self.headers.get("Authorization")):
                return True
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")
            if path == "/health":
                return self._send(HTTPStatus.OK, {"status": "ok"})
            if not self._guard():
                return
            if path == "/units":
                return self._send(HTTPStatus.OK, service.units())
            if path.startswith("/runs/"):
                job = service.get(path.split("/", 2)[2])
                if job is None:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "unknown job"})
                return self._send(HTTPStatus.OK, job.snapshot())
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if parts == ["runs"]:
                level, unit_id = "agent", None
            elif len(parts) == 3 and parts[0] in ("divisions", "specialists") and parts[2] == "runs":
                level, unit_id = parts[0][:-1], parts[1]
            else:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if not self._guard():
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "missing or oversized body"})
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                request = service.validate(payload)
            except PermissionError as exc:
                return self._send(HTTPStatus.FORBIDDEN, {"error": str(exc)})
            except (ValueError, UnicodeDecodeError) as exc:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            wait = parse_qs(parsed.query).get("wait", ["false"])[0].lower() in ("1", "true", "yes")
            try:
                job = service.start(request, level=level, unit_id=unit_id, wait=wait)
            except ValueError as exc:
                return self._send(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            self._send(HTTPStatus.OK if wait else HTTPStatus.ACCEPTED, job.snapshot())

    return Handler


def serve(service: CodingAgentService, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    LOGGER.info("coding agent API listening on http://%s:%d", host, port)
    print(f"coding agent API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
