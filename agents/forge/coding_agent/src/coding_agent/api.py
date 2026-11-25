"""HTTP service for any unit (standard library only).

Serve the whole agent, or any single unit on its own port:

    coding-agent serve --allow-root D:\\work                                     # the agent
    coding-agent serve --allow-root D:\\work --unit development/database_developer --port 8801

Endpoints (relative to the served unit):

    GET  /health                 liveness
    GET  /units                  the served unit and everything under it
    POST /runs                   run the served unit (body: CodingRequest payload)
    POST /units/<path>/runs      run a unit under the served one
    POST /ask                    ask the served unit a question about a workspace (chat; writes nothing)
    POST /units/<path>/ask       ask a unit under the served one
    GET  /runs/<job_id>          job status, progress events and (when done) report or answer
    POST /proposals/apply        write a proposed run's changes after a human approved them
    POST /invoke                 internal: a parent unit delegating one step to this unit

POST /runs and POST /ask return 202 with a job id; add ?wait=true to block. Workspaces
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
from .core.units import handle_invoke, load_proposal, proposal_conflicts
from .core.workspace import Workspace

LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 8_000_000  # /invoke carries proposed file contents
ASK_FIELDS = frozenset({"question", "workspace", "history", "focus_files"})
MAX_QUESTION_CHARS = 20_000
MAX_HISTORY_TURNS = 20
MAX_TURN_CHARS = 4_000
MAX_FOCUS_FILES = 10


@dataclass
class Job:
    id: str
    kind: str = "run"  # "run" or "ask"
    status: str = "queued"
    events: list[dict] = field(default_factory=list)
    report: dict | None = None
    answer: dict | None = None
    error: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {"job_id": self.id, "kind": self.kind, "status": self.status, "events": list(self.events),
                "report": self.report, "answer": self.answer, "error": self.error}


@dataclass
class AskRequest:
    question: str
    workspace: str
    history: list[dict[str, str]] = field(default_factory=list)
    focus_files: list[str] = field(default_factory=list)


class ProposalConflict(Exception):
    """A proposal that cannot be applied as it is: not proposed, or its files changed since."""

    def __init__(self, message: str, conflicts: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflicts = conflicts or []


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
        self._apply_lock = threading.Lock()

    # ---------------------------------------------------------------- checks
    def authorized(self, header: str | None) -> bool:
        if not self.token:
            return True
        expected = f"Bearer {self.token}"
        return bool(header) and hmac.compare_digest(header.encode(), expected.encode())

    def _check_workspace(self, workspace: str) -> str:
        if not workspace or not workspace.strip():
            raise ValueError("workspace must not be empty")
        resolved = Path(workspace).expanduser().resolve()
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            raise PermissionError("workspace is outside the allowed roots")
        return str(resolved)

    def validate(self, payload: dict[str, Any]) -> CodingRequest:
        request = CodingRequest.from_payload(payload)
        request.workspace = self._check_workspace(request.workspace)
        return request

    def validate_ask(self, payload: dict[str, Any]) -> AskRequest:
        """An ask request from an untrusted JSON payload."""
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        unknown = set(payload) - ASK_FIELDS
        if unknown:
            raise ValueError(f"unknown ask fields: {sorted(unknown)}")
        question = str(payload.get("question") or "").strip()
        if not question:
            raise ValueError("question must not be empty")
        if len(question) > MAX_QUESTION_CHARS:
            raise ValueError(f"question is longer than {MAX_QUESTION_CHARS} characters")
        history = payload.get("history") or []
        if not isinstance(history, list) or not all(
                isinstance(t, dict) and t.get("role") in ("user", "agent") and isinstance(t.get("text"), str)
                for t in history):
            raise ValueError("history must be a list of {role: 'user' | 'agent', text: string}")
        focus = payload.get("focus_files") or []
        if not isinstance(focus, list) or not all(isinstance(f, str) for f in focus):
            raise ValueError("focus_files must be a list of strings")
        return AskRequest(
            question=question, workspace=self._check_workspace(str(payload.get("workspace") or "")),
            history=[{"role": t["role"], "text": t["text"][:MAX_TURN_CHARS]} for t in history[-MAX_HISTORY_TURNS:]],
            focus_files=focus[:MAX_FOCUS_FILES],
        )

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

    def ask(self, request: AskRequest, *, unit: str | None = None, wait: bool = False) -> Job:
        target = self.resolve(unit)
        target.answerer()  # a unit with nobody to answer fails here, not in the worker
        job = Job(id=uuid.uuid4().hex[:12], kind="ask")
        with self._lock:
            self.jobs[job.id] = job

        def emit(event: dict) -> None:
            with self._lock:
                job.events.append(event)

        def work() -> None:
            job.status = "running"
            invocation = InvocationContext(source=InvocationSource.API, caller_id="api")
            try:
                answer = target.ask(request.question, request.workspace, invocation, history=request.history,
                                    focus_files=request.focus_files, emit=emit)
                job.answer = answer.to_dict()
                job.status, job.error = ("failed", answer.error) if answer.error else ("completed", "")
            except Exception as exc:  # pragma: no cover - ask() reports model failures itself
                LOGGER.exception("ask %s failed", job.id)
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"

        if wait:
            work()
        else:
            threading.Thread(target=work, name=f"coding-agent-ask-{job.id}", daemon=True).start()
        return job

    def apply_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write a proposed run's changes: ``{workspace, run_id, force?}``.

        The proposal is read from the run's folder, so it survives a restart of this service. Files
        edited on disk since the team changed them are a conflict unless ``force`` is true; the
        overwritten versions are backed up in the run folder either way.
        """
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        unknown = set(payload) - {"workspace", "run_id", "force"}
        if unknown:
            raise ValueError(f"unknown fields: {sorted(unknown)}")
        workspace = Workspace(self._check_workspace(str(payload.get("workspace") or "")))
        run_id = str(payload.get("run_id") or "")
        with self._apply_lock:
            report = load_proposal(workspace, run_id)
            if report.status != "proposed":
                raise ProposalConflict(f"run {run_id} is {report.status}; only proposed runs can be applied")
            conflicts = proposal_conflicts(workspace, report)
            if conflicts and payload.get("force") is not True:
                raise ProposalConflict("files changed on disk since the team proposed its changes", conflicts)
            invocation = InvocationContext(source=InvocationSource.API, caller_id="api")
            report = self.agent.apply_proposal(report, invocation)
        return {"run_id": report.run_id, "status": report.status, "written": [c.path for c in report.changes],
                "notes": report.notes, "artifacts_dir": report.artifacts_dir}

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
            elif parts == ["proposals", "apply"]:
                kind, unit = "apply", None
            elif parts in (["runs"], ["ask"]):
                kind, unit = ("run" if parts == ["runs"] else "ask"), None
            elif len(parts) >= 3 and parts[0] == "units" and parts[-1] in ("runs", "ask"):
                kind, unit = ("run" if parts[-1] == "runs" else "ask"), "/".join(parts[1:-1])
            else:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if not self._guard():
                return
            try:
                payload = self._body()
                if kind == "invoke":
                    return self._send(HTTPStatus.OK, service.invoke(payload))
                if kind == "apply":
                    return self._send(HTTPStatus.OK, service.apply_proposal(payload))
                wait = parse_qs(parsed.query).get("wait", ["false"])[0].lower() in ("1", "true", "yes")
                if kind == "ask":
                    job = service.ask(service.validate_ask(payload), unit=unit, wait=wait)
                else:
                    job = service.start(service.validate(payload), unit=unit, wait=wait)
            except ProposalConflict as exc:
                return self._send(HTTPStatus.CONFLICT, {"error": str(exc), "conflicts": exc.conflicts})
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
