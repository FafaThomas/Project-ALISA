"""Standalone units, team specialists, per-unit models and units served on their own."""

import json
import shutil
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest
from conftest import CALC, ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource, load_unit
from coding_agent.api import CodingAgentService, make_handler, missing_requirements
from coding_agent.core.llm import ModelRouter
from coding_agent.core.registry import PACKAGE_ROOT, Tree

UI = InvocationContext(source=InvocationSource.UI)
PG_SQL = "CREATE TABLE users (id SERIAL PRIMARY KEY);\n"
QDRANT_PY = "COLLECTION = {'name': 'docs', 'size': 768}\n"


def write_unit(folder, front: str, body: str = "# Unit\nDoes things.") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "unit.md").write_text(f"---\n{front.strip()}\n---\n{body}\n", encoding="utf-8")


@pytest.fixture
def pkg(tmp_path):
    """A copy of the packaged tree where the Database Developer has become a team."""
    root = tmp_path / "pkg" / "coding_agent"
    shutil.copytree(PACKAGE_ROOT / "divisions", root / "divisions")
    shutil.copy(PACKAGE_ROOT / "unit.md", root / "unit.md")
    db = root / "divisions" / "development" / "database_developer"
    write_unit(db / "postgres_developer", 'id: postgres_developer\nname: Postgres Developer\nkind: builder\n'
               'responsibility: PostgreSQL schemas and queries\ntriggers: ["postgres", "postgresql"]\n'
               'model: pg-model')
    write_unit(db / "qdrant_developer", 'id: qdrant_developer\nname: Qdrant Developer\nkind: builder\n'
               'responsibility: Qdrant collections and vector search\ntriggers: ["qdrant"]')
    return root


def builder_reply(path, content):
    return {"summary": f"wrote {path}", "files": [{"path": path, "action": "create", "content": content}]}


TEAM_HANDLERS = {
    "postgres_developer": builder_reply("db/schema.sql", PG_SQL),
    "qdrant_developer": builder_reply("vectors/collection.py", QDRANT_PY),
    "database_developer": lambda p, s: (
        {"summary": "split", "work_items": [
            {"id": "a", "title": "postgres schema", "owner_role": "postgres_developer", "description": "",
             "files": ["db/schema.sql"]},
            {"id": "b", "title": "qdrant collection", "owner_role": "qdrant_developer", "description": "",
             "files": ["vectors/collection.py"]}]}
        if "work_items" in (s or {}).get("properties", {}) else builder_reply("db/generic.sql", "SELECT 1;\n")
    ),
}


def team_agent(pkg, handlers=TEAM_HANDLERS):
    llm = ScriptedLLM(handlers)
    return CodingAgent.with_client(llm, tree=Tree.load(pkg)), llm


# ------------------------------------------------------------- standalone units
def test_load_unit_runs_one_specialist_without_the_agent(tmp_path):
    llm = ScriptedLLM({"python_developer": builder_reply("calculator.py", CALC)})
    unit = load_unit("python_developer", router=ModelRouter.single(llm))
    report = unit.run(CodingRequest(task="Write a calculator module", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert (report.unit_level, report.unit_id) == ("specialist", "python_developer")
    assert llm.roles_called() == ["python_developer"]
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == CALC


# ------------------------------------------------------------- team specialists
def test_team_routes_to_the_matching_sub_specialist(pkg, tmp_path):
    agent, llm = team_agent(pkg)
    report = agent.unit("database_developer").run(
        CodingRequest(task="Create the postgres schema for users", workspace=str(tmp_path / "w")), UI)
    assert report.status == "completed", report.error
    assert llm.roles_called() == ["postgres_developer"]
    assert [c.author_role for c in report.changes] == ["postgres_developer"]


def test_team_splits_work_when_several_sub_specialists_match(pkg, tmp_path):
    agent, llm = team_agent(pkg)
    report = agent.unit("database_developer").run(
        CodingRequest(task="Set up a postgres schema and a qdrant collection", workspace=str(tmp_path / "w")), UI)
    assert report.status == "completed", report.error
    assert llm.roles_called() == ["database_developer", "postgres_developer", "qdrant_developer"]
    lead_call = llm.calls[0]
    assert "lead of your team" in lead_call["system"]
    assert sorted(c.path for c in report.changes) == ["db/schema.sql", "vectors/collection.py"]


def test_team_generalist_handles_unmatched_work(pkg, tmp_path):
    agent, llm = team_agent(pkg)
    report = agent.unit("database_developer").run(
        CodingRequest(task="Write a SQL health check query", workspace=str(tmp_path / "w")), UI)
    assert report.status == "completed", report.error
    assert llm.roles_called() == ["database_developer"]


def test_sub_specialist_is_callable_directly_and_knows_its_team(pkg, tmp_path):
    agent, llm = team_agent(pkg)
    report = agent.unit("development/database_developer/postgres_developer").run(
        CodingRequest(task="Add an index on users.email", workspace=str(tmp_path / "w")), UI)
    assert report.status == "completed", report.error
    prompt = llm.calls[0]
    assert "Development division, Database Developer team" in prompt["system"]
    assert "## Your team: Database Developer" in prompt["prompt"]


def test_tech_lead_assigns_work_to_the_team_as_one_specialist(pkg, tmp_path):
    handlers = dict(TEAM_HANDLERS)
    handlers["engineering_director"] = {"rationale": "db work", "team": ["database_developer"]}
    handlers["tech_lead"] = {"summary": "one item", "work_items": [
        {"id": "W1", "title": "postgres schema", "owner_role": "database_developer", "description": "",
         "files": ["db/schema.sql"]}]}
    agent, llm = team_agent(pkg, handlers)
    report = agent.run(CodingRequest(task="Create a postgres schema for users", workspace=str(tmp_path / "w")), UI)
    assert report.status == "completed", report.error
    lead_prompt = next(c["prompt"] for c in llm.calls if c["role"] == "tech_lead")
    assert "- database_developer:" in lead_prompt and "postgres_developer:" not in lead_prompt
    assert "postgres_developer" in llm.roles_called()


# ------------------------------------------------------------- per-unit models
def test_each_unit_reports_its_model_and_where_it_came_from(pkg, tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CODING_AGENT_CODING_MODEL", "env-coder")
    monkeypatch.setenv("FORGE_CODING_AGENT_PLANNING_MODEL", "env-planner")
    made = {}

    def factory(model):
        made[model] = ScriptedLLM(TEAM_HANDLERS, model=model)
        return made[model]

    agent = CodingAgent(ModelRouter(client_factory=factory), tree=Tree.load(pkg))
    report = agent.unit("database_developer").run(
        CodingRequest(task="Set up a postgres schema and a qdrant collection", workspace=str(tmp_path / "w")), UI)
    assert report.status == "completed", report.error
    by_role = {}
    for output in report.outputs:
        for change in output.changes:
            by_role[change.author_role] = (output, change)
    merged = report.outputs[0]
    assert "pg-model" in merged.model and "env-coder" in merged.model
    assert "unit.md (development/database_developer/postgres_developer)" in merged.model_source
    assert ".env FORGE_CODING_AGENT_CODING_MODEL" in merged.model_source
    assert made["pg-model"].roles_called() == ["postgres_developer"]
    assert set(made["env-coder"].roles_called()) >= {"qdrant_developer"}


# ------------------------------------------------------------- remote units
def start_service(service):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_parent_calls_a_unit_served_on_its_own(pkg, tmp_path):
    # The Postgres Developer runs as its own service with its own model client.
    remote_llm = ScriptedLLM(TEAM_HANDLERS)
    remote_agent = CodingAgent.with_client(remote_llm, tree=Tree.load(pkg))
    httpd, url = start_service(CodingAgentService(remote_agent, [tmp_path], token="", unit="postgres_developer"))
    try:
        unit_md = pkg / "divisions/development/database_developer/postgres_developer/unit.md"
        unit_md.write_text(unit_md.read_text(encoding="utf-8").replace("model: pg-model", f"endpoint: {url}"),
                           encoding="utf-8")
        local_llm = ScriptedLLM(TEAM_HANDLERS)
        agent = CodingAgent.with_client(local_llm, tree=Tree.load(pkg))
        events = []
        report = agent.unit("database_developer").run(
            CodingRequest(task="Create the postgres schema", workspace=str(tmp_path / "w")), UI, emit=events.append)
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert report.status == "completed", report.error
    assert remote_llm.roles_called() == ["postgres_developer"]
    assert "postgres_developer" not in local_llm.roles_called()
    assert (tmp_path / "w" / "db" / "schema.sql").read_text(encoding="utf-8") == PG_SQL
    assert f"via {url}" in report.outputs[0].model_source
    assert any(e.get("event") == "team_routed" for e in events)


def test_remote_unit_failure_is_reported_not_raised(pkg, tmp_path):
    unit_md = pkg / "divisions/development/database_developer/postgres_developer/unit.md"
    unit_md.write_text(unit_md.read_text(encoding="utf-8").replace("model: pg-model", "endpoint: http://127.0.0.1:9"),
                       encoding="utf-8")
    agent, _ = team_agent(pkg)
    report = agent.unit("database_developer").run(
        CodingRequest(task="Create the postgres schema", workspace=str(tmp_path / "w")), UI)
    assert report.status == "failed"
    assert "remote unit http://127.0.0.1:9/invoke failed" in report.outputs[0].error


def test_single_unit_service_only_serves_its_subtree(pkg, tmp_path):
    agent, _ = team_agent(pkg)
    httpd, url = start_service(CodingAgentService(agent, [tmp_path], token="", unit="database_developer"))

    def call(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(url + path, data=data, headers={"Content-Type": "application/json"},
                      method="POST" if body is not None else "GET")
        try:
            with urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except Exception as exc:  # HTTPError
            return exc.code, json.loads(exc.read())

    try:
        status, tree = call("/units")
        assert status == 200 and tree["id"] == "database_developer"
        assert [u["id"] for u in tree["units"]] == ["postgres_developer", "qdrant_developer"]
        status, job = call("/runs?wait=true", {"task": "Create the postgres schema", "workspace": str(tmp_path / "s")})
        assert status == 200 and job["status"] == "completed", job
        assert job["report"]["unit_id"] == "database_developer"
        status, _ = call("/units/development/database_developer/postgres_developer/runs?wait=true",
                         {"task": "postgres index", "workspace": str(tmp_path / "s2")})
        assert status == 200
        status, body = call("/units/security/runs", {"task": "x", "workspace": str(tmp_path)})
        assert status == 404 and "not served here" in body["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_missing_unit_requirements_are_reported(pkg):
    folder = pkg / "divisions/development/database_developer/qdrant_developer"
    (folder / "requirements.txt").write_text("pytest>=7\nsurely-not-installed-pkg-xyz==1.0\n", encoding="utf-8")
    agent, _ = team_agent(pkg)
    missing = missing_requirements(agent.unit("database_developer"))
    assert len(missing) == 1 and missing[0].startswith("surely-not-installed-pkg-xyz")
    assert agent.unit("qdrant_developer").describe()["has_requirements"] is True
