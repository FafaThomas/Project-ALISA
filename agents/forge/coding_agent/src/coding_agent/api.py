"""HTTP service for any unit (standard library only).

Serve the whole agent, or any single unit on its own port:

    coding-agent serve --allow-root D:\\work                                     # the agent
    coding-agent serve --allow-root D:\\work --unit development/database_developer --port 8801

Endpoints (relative to the served unit):

    GET  /health                 liveness
    GET  /units                  the served unit and everything under it
    POST /runs                   run the served unit (body: CodingRequest payload)
    POST /units/<path>/runs      run a unit under the served one
    GET  /runs/<job_id>          job status, progress events and (when done) report
    POST /invoke                 internal: a parent unit delegating one step to this unit

POST /runs returns 202 with a job id; add ?wait=true to block. Workspaces
must sit under an allowed root. A bearer token is required when ``token`` is
set (or ``CODING_AGENT_API_TOKEN`` is in the environment); parents send the
same variable when calling remote units.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent import CodingAgent, Unit
from .core.models import CodingRequest, InvocationContext, InvocationSource
from .core.units import handle_invoke

LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 8_000_000  # /invoke carries proposed file contents


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


def missing_requirements(unit: Unit) -> list[str]:
    """Packages listed in requirements.txt files under ``unit`` that are not installed."""
    missing = []
    for spec in unit.tree.subtree(unit.spec):
        path = spec.requirements_file
        if path is None:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            match = re.match(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*", line)
            if not match:
                continue
            try:
                metadata.version(match.group(0))
            except metadata.PackageNotFoundError:
                missing.append(f"{match.group(0)} ({path})")
    return missing


class CodingAgentService:
    """Transport-independent request handling; the HTTP layer is a thin shell."""

    def __init__(
        self, agent: CodingAgent, allowed_roots: list[str | Path], token: str | None = None,
        unit: str | None = None,
    ) -> None:
        if not allowed_roots:
            raise ValueError("at least one allowed workspace root is required")
        self.agent = agent
        self.served = agent.unit(unit)
        self.allowed_roots = [Path(r).expanduser().resolve() for r in allowed_roots]
        self.token = token if token is not None else os.getenv("CODING_AGENT_API_TOKEN") or None
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- checks
    def authorized(self, header: str | None) -> bool:
        if not self.token:
            return True
        expected = f"Bearer {self.token}"
        return bool(header) and hmac.compare_digest(header.encode(), expected.encode())

    def _check_workspace(self, workspace: str) -> str:
        resolved = Path(workspace).expanduser().resolve()
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            raise PermissionError("workspace is outside the allowed roots")
        return str(resolved)

    def validate(self, payload: dict[str, Any]) -> CodingRequest:
        request = CodingRequest.from_payload(payload)
        request.workspace = self._check_workspace(request.workspace)
        return request

    def resolve(self, ref: str | None) -> Unit:
        """A unit within the served subtree (by path relative to the tree, or id)."""
        if not ref:
            return self.served
        unit = self.agent.unit(ref)
        base = self.served.path
        if base and unit.path != base and not unit.path.startswith(base + "/"):
            raise LookupError(f"unit {ref!r} is not served here")
        return unit

    # ------------------------------------------------------------------ runs
    def start(self, request: CodingRequest, *, unit: str | None = None, wait: bool = False) -> Job:
        target = self.resolve(unit)
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
                report = target.run(request, invocation, emit=emit)
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

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        unit = self.resolve(str(payload.get("unit") or self.served.path))
        request = payload.get("request")
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        request["workspace"] = self._check_workspace(str(request.get("workspace", "")))
        payload["unit"] = unit.path
        return handle_invoke(self.agent.tree, self.agent.router, payload)

    def units(self) -> dict[str, Any]:
        return self.served.describe()


def make_handler(service: CodingAgentService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ALISA-CodingAgent/0.4"

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

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("missing or oversized body")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")
            if path == "/health":
                return self._send(HTTPStatus.OK, {"status": "ok", "unit": service.served.path or service.served.unit_id})
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
            if parts == ["invoke"]:
                kind, unit = "invoke", None
            elif parts == ["runs"]:
                kind, unit = "run", None
            elif len(parts) >= 3 and parts[0] == "units" and parts[-1] == "runs":
                kind, unit = "run", "/".join(parts[1:-1])
            else:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if not self._guard():
                return
            try:
                payload = self._body()
                if kind == "invoke":
                    return self._send(HTTPStatus.OK, service.invoke(payload))
                request = service.validate(payload)
                wait = parse_qs(parsed.query).get("wait", ["false"])[0].lower() in ("1", "true", "yes")
                job = service.start(request, unit=unit, wait=wait)
            except PermissionError as exc:
                return self._send(HTTPStatus.FORBIDDEN, {"error": str(exc)})
            except LookupError as exc:
                return self._send(HTTPStatus.NOT_FOUND, {"error": str(exc).strip("'\"")})
            except (ValueError, UnicodeDecodeError) as exc:
                status = HTTPStatus.NOT_FOUND if "unknown unit" in str(exc) else HTTPStatus.BAD_REQUEST
                return self._send(status, {"error": str(exc)})
            self._send(HTTPStatus.OK if wait else HTTPStatus.ACCEPTED, job.snapshot())

    return Handler


def serve(service: CodingAgentService, host: str = "127.0.0.1", port: int = 8765) -> None:
    missing = missing_requirements(service.served)
    for item in missing:
        print(f"warning: requirement not installed: {item}")
    server = ThreadingHTTPServer((host, port), make_handler(service))
    label = service.served.path or service.served.unit_id
    print(f"serving unit '{label}' on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
