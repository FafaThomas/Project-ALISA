import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from conftest import ScriptedLLM, calculator_handlers

from coding_agent import CodingAgent
from coding_agent.api import CodingAgentService, make_handler


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    service = CodingAgentService(CodingAgent.with_client(ScriptedLLM(calculator_handlers())),
                                 allowed_roots=[root], token="s3cret")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None, token="s3cret"):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_health_is_public_and_the_rest_needs_a_token(server):
    base, _ = server
    assert call(f"{base}/health", token=None) == (200, {"status": "ok"})
    assert call(f"{base}/units", token=None)[0] == 401
    status, tree = call(f"{base}/units")
    assert status == 200 and len(tree["divisions"]) == 7
    assert sum(len(d["specialists"]) for d in tree["divisions"]) == 51


def test_run_over_http(server):
    base, root = server
    status, job = call(f"{base}/runs?wait=true", "POST", {
        "task": "Build a calculator CLI in Python", "workspace": str(root / "calc"),
    })
    assert status == 200, job
    assert job["status"] == "completed"
    assert job["report"]["invocation_source"] == "api"
    assert job["report"]["context_strategy"] == "markdown"
    assert (root / "calc" / "calculator.py").exists()
    assert any(e["event"] == "team" for e in job["events"])
    assert call(f"{base}/runs/{job['job_id']}")[1]["status"] == "completed"


def test_async_run_returns_a_job(server):
    base, root = server
    status, job = call(f"{base}/runs", "POST", {"task": "Build a calculator CLI in Python",
                                                 "workspace": str(root / "async")})
    assert status == 202 and job["job_id"]


def test_workspace_must_be_under_an_allowed_root(server, tmp_path):
    base, _ = server
    status, body = call(f"{base}/runs?wait=true", "POST", {"task": "x", "workspace": str(tmp_path / "elsewhere")})
    assert status == 403
    status, body = call(f"{base}/runs?wait=true", "POST", {"task": "x", "workspace": str(tmp_path / "allowed" / ".." / "elsewhere")})
    assert status == 403


def test_division_and_specialist_routes(server):
    base, root = server
    status, job = call(f"{base}/divisions/development/runs?wait=true", "POST", {
        "task": "Build a calculator CLI in Python", "workspace": str(root / "div"),
    })
    assert status == 200 and job["status"] == "completed", job
    assert (job["report"]["unit_level"], job["report"]["unit_id"]) == ("division", "development")

    status, job = call(f"{base}/specialists/engineering_director/runs?wait=true", "POST", {
        "task": "Build a calculator CLI in Python", "workspace": str(root / "spec"),
    })
    assert status == 200 and job["report"]["unit_id"] == "engineering_director"

    assert call(f"{base}/divisions/marketing/runs", "POST", {"task": "x", "workspace": str(root)})[0] == 404
    assert call(f"{base}/specialists/wizard/runs", "POST", {"task": "x", "workspace": str(root)})[0] == 404


def test_bad_payloads_are_rejected(server):
    base, root = server
    assert call(f"{base}/runs", "POST", {"task": "x", "workspace": str(root), "knowledge": "x"})[0] == 400
    assert call(f"{base}/runs", "POST", {"workspace": str(root)})[0] == 400
    assert call(f"{base}/runs/nope")[0] == 404
