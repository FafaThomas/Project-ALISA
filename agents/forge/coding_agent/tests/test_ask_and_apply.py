"""Chat questions (POST /ask) and approving a proposal later (POST /proposals/apply), as an editor uses them."""

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from conftest import CALC, ScriptedLLM, calculator_handlers

from coding_agent import CodingAgent
from coding_agent.api import CodingAgentService, make_handler
from coding_agent.core.registry import default_tree

TOKEN = "s3cret"


@pytest.fixture
def served(tmp_path):
    """A live service plus the scripted model behind it, so tests can read the prompts it was sent."""
    root = tmp_path / "allowed"
    root.mkdir()
    handlers = calculator_handlers()
    handlers["codebase_analyst"] = {
        "answer": "`app.py` defines `main()`, which prints a greeting.",
        "files": ["app.py", "./app.py", "missing.py", "../outside.txt", ".env"],
    }
    llm = ScriptedLLM(handlers)
    service = CodingAgentService(CodingAgent.with_client(llm), allowed_roots=[root], token=TOKEN)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root, llm
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def project(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir()
    (folder / "app.py").write_text('def main():\n    print("hello from APP_MARKER")\n', encoding="utf-8")
    (folder / ".env").write_text("SECRET=do-not-send\n", encoding="utf-8")
    return folder


# ------------------------------------------------------------------ ask
def test_ask_answers_from_the_workspace_and_writes_nothing(served):
    base, root, llm = served
    folder = project(root, "chat")
    status, job = call(f"{base}/ask?wait=true", "POST", {
        "question": "What does main do?", "workspace": str(folder), "focus_files": ["app.py", ".env"],
        "history": [{"role": "user", "text": "Earlier question HISTORY_MARKER"},
                    {"role": "agent", "text": "Earlier answer"}],
    })
    assert status == 200, job
    assert (job["kind"], job["status"], job["report"]) == ("ask", "completed", None)
    answer = job["answer"]
    assert answer["role"] == "codebase_analyst"
    assert "main()" in answer["answer"]
    # Only files that exist inside the workspace (and are not secrets) come back, once each.
    assert answer["files"] == ["app.py"]
    assert any(".env" in note for note in answer["notes"])
    assert [e["event"] for e in job["events"]] == ["ask_started", "ask_finished"]

    (asked,) = llm.calls
    assert asked["role"] == "codebase_analyst"
    assert "question about their project" in asked["system"]
    assert asked["schema"]["required"] == ["answer", "files"]
    assert "What does main do?" in asked["prompt"]
    assert "APP_MARKER" in asked["prompt"]          # the open file is shared
    assert "HISTORY_MARKER" in asked["prompt"]      # earlier turns are shared
    assert "do-not-send" not in asked["prompt"]     # the secrets file is not

    # Chat leaves no run folder and no memory entry behind.
    assert not (folder / ".coding-agent").exists()


def test_ask_goes_to_the_unit_asked(served):
    base, root, llm = served
    folder = project(root, "units")
    status, job = call(f"{base}/units/security/ask?wait=true", "POST",
                       {"question": "Is this safe?", "workspace": str(folder)})
    assert status == 200 and job["answer"]["role"] == default_tree().get("security").lead
    status, job = call(f"{base}/units/development/python_developer/ask?wait=true", "POST",
                       {"question": "How would you test main?", "workspace": str(folder)})
    assert status == 200 and job["answer"]["role"] == "python_developer"
    assert call(f"{base}/units/marketing/ask", "POST", {"question": "x", "workspace": str(folder)})[0] == 404


def test_ask_rejects_bad_payloads(served, tmp_path):
    base, root, _ = served
    folder = project(root, "bad")
    assert call(f"{base}/ask", "POST", {"question": " ", "workspace": str(folder)})[0] == 400
    assert call(f"{base}/ask", "POST", {"question": "x"})[0] == 400
    assert call(f"{base}/ask", "POST", {"question": "x", "workspace": str(folder), "task": "x"})[0] == 400
    assert call(f"{base}/ask", "POST", {"question": "x", "workspace": str(folder),
                                        "history": [{"role": "system", "text": "x"}]})[0] == 400
    assert call(f"{base}/ask", "POST", {"question": "x", "workspace": str(tmp_path)})[0] == 403


def test_ask_about_a_missing_workspace_fails_cleanly(served):
    base, root, _ = served
    status, job = call(f"{base}/ask?wait=true", "POST", {"question": "x", "workspace": str(root / "nope")})
    assert status == 200 and job["status"] == "failed" and "does not exist" in job["error"]


# ----------------------------------------------------------- run history
def test_a_run_sees_the_chat_before_its_task(served):
    base, root, llm = served
    workspace = root / "follow-up"
    status, job = call(f"{base}/runs?wait=true", "POST", {
        "task": "lets fix it", "workspace": str(workspace), "mode": "propose",
        "history": [{"role": "user", "text": "Build a calculator CLI in Python"},
                    {"role": "agent", "text": "Code run: failed. Files gate problems: calculator.py RUN_HISTORY_MARKER"}],
    })
    assert status == 200 and job["status"] == "proposed", job
    # Every model call of the run, the Director's team pick included, knows what "it" is.
    assert len(llm.calls) > 3
    for made in llm.calls:
        assert "RUN_HISTORY_MARKER" in made["prompt"], made["role"]
        assert made["prompt"].index("lets fix it") < made["prompt"].index("RUN_HISTORY_MARKER")
    # The chat informs the run; it is not recorded with it.
    assert "RUN_HISTORY_MARKER" not in json.dumps(job["report"])
    assert "RUN_HISTORY_MARKER" not in (Path(job["report"]["artifacts_dir"]) / "report.json").read_text(encoding="utf-8")

    bad = {"task": "x", "workspace": str(workspace), "history": [{"role": "system", "text": "x"}]}
    assert call(f"{base}/runs", "POST", bad)[0] == 400


def test_run_history_is_bounded_and_survives_delegation():
    from coding_agent.core.models import CodingRequest, request_to_dict
    from coding_agent.core.prompts import RUN_HISTORY_CHARS, RUN_TURN_CHARS, conversation_md

    turns = [{"role": "user" if i % 2 == 0 else "agent", "text": f"turn {i} " + "x" * 5_000} for i in range(30)]
    request = CodingRequest.from_payload({"task": "again", "workspace": "D:/w", "history": turns})
    assert len(request.history) == 20 and request.history[-1]["text"].startswith("turn 29")
    rendered = conversation_md(request.history)
    assert "turn 29" in rendered and "turn 28" in rendered and "turn 20" not in rendered
    assert len(rendered) < RUN_HISTORY_CHARS + 2 * RUN_TURN_CHARS
    assert rendered.index("turn 28") < rendered.index("turn 29")  # oldest first
    assert conversation_md([]) == ""
    # A unit delegating to another sends the request as a dict; the chat goes with it.
    assert CodingRequest.from_payload(request_to_dict(request)).history == request.history


# ---------------------------------------------------------------- apply
def propose(base: str, workspace: Path) -> dict:
    status, job = call(f"{base}/runs?wait=true", "POST", {
        "task": "Build a calculator CLI in Python", "workspace": str(workspace), "mode": "propose",
    })
    assert status == 200 and job["status"] == "proposed", job
    return job["report"]


def test_a_proposal_is_applied_later_from_its_run_folder(served):
    base, root, _ = served
    workspace = root / "calc"
    report = propose(base, workspace)
    assert not (workspace / "calculator.py").exists()
    run_dir = Path(report["artifacts_dir"])
    assert json.loads((run_dir / "proposed-base.json").read_text()) == {"calculator.py": None, "main.py": None}

    status, result = call(f"{base}/proposals/apply", "POST", {"workspace": str(workspace), "run_id": report["run_id"]})
    assert status == 200, result
    assert result["status"] == "completed"
    assert sorted(result["written"]) == ["calculator.py", "main.py"]
    assert (workspace / "calculator.py").read_text() == CALC
    assert (workspace / "CHANGELOG.md").is_file()
    assert json.loads((run_dir / "report.json").read_text())["status"] == "completed"

    # A run is applied once.
    status, body = call(f"{base}/proposals/apply", "POST", {"workspace": str(workspace), "run_id": report["run_id"]})
    assert status == 409 and "completed" in body["error"]


def test_edits_to_the_proposed_files_are_what_gets_applied(served):
    base, root, _ = served
    workspace = root / "edited"
    report = propose(base, workspace)
    proposed = Path(report["artifacts_dir"]) / "proposed" / "calculator.py"
    proposed.write_bytes(CALC.replace("a + b", "b + a").replace("\n", "\r\n").encode())
    status, _ = call(f"{base}/proposals/apply", "POST", {"workspace": str(workspace), "run_id": report["run_id"]})
    assert status == 200
    assert (workspace / "calculator.py").read_bytes() == CALC.replace("a + b", "b + a").replace("\n", "\r\n").encode()


def test_files_changed_since_the_proposal_are_a_conflict_until_forced(served):
    base, root, _ = served
    workspace = root / "conflict"
    report = propose(base, workspace)
    (workspace / "calculator.py").write_text("# my own version\n", encoding="utf-8")
    body = {"workspace": str(workspace), "run_id": report["run_id"]}

    status, result = call(f"{base}/proposals/apply", "POST", body)
    assert status == 409 and result["conflicts"] == ["calculator.py"]
    assert (workspace / "calculator.py").read_text() == "# my own version\n"
    assert not (workspace / "main.py").exists()  # nothing is written on a conflict

    status, result = call(f"{base}/proposals/apply", "POST", {**body, "force": True})
    assert status == 200
    assert (workspace / "calculator.py").read_text() == CALC
    backup = Path(report["artifacts_dir"]) / "backup" / "calculator.py"
    assert backup.read_text() == "# my own version\n"


def test_apply_rejects_bad_requests(served, tmp_path):
    base, root, _ = served
    workspace = root / "calc2"
    report = propose(base, workspace)
    apply = f"{base}/proposals/apply"
    assert call(apply, "POST", {"workspace": str(workspace), "run_id": "../../etc"})[0] == 400
    assert call(apply, "POST", {"workspace": str(workspace), "run_id": "20990101-000000-abcdef"})[0] == 404
    assert call(apply, "POST", {"workspace": str(tmp_path), "run_id": report["run_id"]})[0] == 403
    assert call(apply, "POST", {"workspace": str(workspace), "run_id": report["run_id"], "x": 1})[0] == 400
    assert call(apply, "POST", {"run_id": report["run_id"]})[0] == 400
    # A failed run's files stay a proposal nobody can apply.
    run_dir = Path(report["artifacts_dir"])
    data = json.loads((run_dir / "report.json").read_text())
    (run_dir / "report.json").write_text(json.dumps({**data, "status": "failed"}))
    status, body = call(apply, "POST", {"workspace": str(workspace), "run_id": report["run_id"]})
    assert status == 409 and "failed" in body["error"]
