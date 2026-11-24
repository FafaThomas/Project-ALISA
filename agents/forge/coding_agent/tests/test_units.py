"""Calling the hierarchy at each level: agent, division, specialist."""

from conftest import CALC, MAIN, ScriptedLLM, calculator_handlers

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource

UI = InvocationContext(source=InvocationSource.UI)


def agent_with(handlers=None):
    llm = ScriptedLLM(handlers or {})
    return CodingAgent.with_client(llm), llm


def seed(tmp_path):
    (tmp_path / "app.py").write_text(
        "import subprocess\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("requests\nflask==3.0.0\n", encoding="utf-8")


# --------------------------------------------------------------- division level
def test_security_division_reviews_existing_code_without_writing(tmp_path):
    seed(tmp_path)
    agent, llm = agent_with()
    report = agent.division("security").run(
        CodingRequest(task="Audit the code and its dependencies", workspace=str(tmp_path)), UI)

    assert report.status == "completed", report.error
    assert (report.unit_level, report.unit_id) == ("division", "security")
    assert set(llm.roles_called()) == {"code_security_auditor", "dependency_auditor"}
    issues = {f.issue for f in report.advisory_findings}
    assert any("shell=True" in i for i in issues)           # code_security_auditor/tools.py
    assert any("requests has no version" in i for i in issues)  # dependency_auditor/tools.py
    assert not report.changes
    auditor_prompt = next(c["prompt"] for c in llm.calls if c["role"] == "dependency_auditor")
    assert "Existing workspace files" in auditor_prompt and "Dependency Auditor tools" in auditor_prompt
    assert "Security Engineering" in auditor_prompt  # division brief is passed down


def test_development_division_builds_with_its_lead(tmp_path):
    agent, llm = agent_with(calculator_handlers())
    report = agent.division("development").run(
        CodingRequest(task="Build a calculator CLI in Python", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert llm.roles_called() == ["tech_lead", "python_developer", "python_developer"]
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == CALC


def test_division_with_two_builders_uses_its_lead_to_split_work(tmp_path):
    (tmp_path / "calc.py").write_text(CALC, encoding="utf-8")
    handlers = {
        "qa_lead": {"summary": "split", "work_items": [
            {"id": "T1", "title": "unit tests", "owner_role": "unit_testing_engineer", "description": "",
             "files": ["tests/test_calc.py"]},
            {"id": "T2", "title": "e2e", "owner_role": "e2e_testing_engineer", "description": "",
             "files": ["tests/test_e2e.py"]},
        ]},
        "unit_testing_engineer": {"summary": "u", "files": [
            {"path": "tests/test_calc.py", "action": "create", "content": "def test_add():\n    assert True\n"}]},
        "e2e_testing_engineer": {"summary": "e", "files": [
            {"path": "tests/test_e2e.py", "action": "create", "content": "def test_flow():\n    assert True\n"}]},
    }
    agent, llm = agent_with(handlers)
    report = agent.division("quality").run(
        CodingRequest(task="Add unit tests and e2e tests with playwright", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert llm.roles_called()[0] == "qa_lead"
    lead_call = llm.calls[0]
    assert "acting as the lead of your division" in lead_call["system"]
    assert "work_items" in lead_call["schema"]["properties"]
    assert sorted(c.path for c in report.changes) == ["tests/test_calc.py", "tests/test_e2e.py"]


def test_division_rejects_roles_from_other_divisions(tmp_path):
    agent, _ = agent_with()
    report = agent.division("security").run(
        CodingRequest(task="x", workspace=str(tmp_path), requested_roles=["python_developer"]), UI)
    assert report.status == "failed" and "not in the security division" in report.error


# ------------------------------------------------------------- specialist level
def test_single_developer_called_directly(tmp_path):
    agent, llm = agent_with({"python_developer": {"summary": "done", "files": [
        {"path": "calculator.py", "action": "create", "content": CALC},
        {"path": "main.py", "action": "create", "content": MAIN}]}})
    report = agent.specialist("python_developer").run(
        CodingRequest(task="Build a calculator CLI in Python", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert (report.unit_level, report.unit_id) == ("specialist", "python_developer")
    assert llm.roles_called() == ["python_developer"]
    assert (tmp_path / "main.py").exists()


def test_director_called_alone_returns_its_team_choice(tmp_path):
    agent, llm = agent_with({"engineering_director": {"rationale": "small job", "team": ["python_developer"]}})
    report = agent.specialist("engineering_director").run(
        CodingRequest(task="Build a calculator CLI in Python", workspace=str(tmp_path)), UI)
    assert report.status == "completed"
    assert llm.roles_called() == ["engineering_director"]
    assert report.outputs[0].decisions == ["Team: engineering_director, python_developer, tech_lead"]
    assert not report.changes


def test_tech_lead_called_alone_reports_a_plan(tmp_path):
    agent, llm = agent_with({"tech_lead": {"summary": "plan", "work_items": [
        {"id": "W1", "title": "core", "owner_role": "python_developer", "description": "", "files": ["a.py"]}]}})
    report = agent.specialist("tech_lead").run(
        CodingRequest(task="Plan a calculator", workspace=str(tmp_path)), UI)
    assert report.status == "completed"
    assert report.outputs[0].decisions == ["core: a.py"]


def test_reviewer_tools_still_run_when_the_model_is_down(tmp_path):
    seed(tmp_path)

    class Down:
        model = "down"

        def generate_json(self, *a, **k):
            from coding_agent.core.llm import LLMError
            raise LLMError("offline")

        def unload(self):
            return True

    report = CodingAgent.with_client(Down()).specialist("code_security_auditor").run(
        CodingRequest(task="audit app.py", workspace=str(tmp_path)), UI)
    assert report.status == "completed"
    assert any("shell=True" in f.issue for f in report.advisory_findings)


def test_unknown_units_are_rejected():
    agent, _ = agent_with()
    for call in (lambda: agent.division("marketing"), lambda: agent.specialist("wizard"),
                 lambda: agent.unit("platoon", "x")):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


# ------------------------------------------------------------------ agent level
def test_bug_fix_attaches_debugging_specialist_to_development(tmp_path):
    (tmp_path / "calculator.py").write_text(CALC.replace("a + b", "a - b"), encoding="utf-8")
    handlers = {
        "engineering_director": {"rationale": "bug fix", "team": ["python_developer", "debugging_specialist"]},
        "tech_lead": {"summary": "fix", "work_items": [
            {"id": "W1", "title": "fix add", "owner_role": "debugging_specialist", "description": "",
             "files": ["calculator.py"]}]},
        "debugging_specialist": {"summary": "fixed", "files": [
            {"path": "calculator.py", "action": "modify", "content": CALC}]},
    }
    agent, llm = agent_with(handlers)
    report = agent.run(CodingRequest(task="Fix the bug: add returns the wrong result", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    lead_prompt = next(c["prompt"] for c in llm.calls if c["role"] == "tech_lead")
    assert "- debugging_specialist:" in lead_prompt  # attached, so the Tech Lead can assign it
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == CALC


def test_forge_context_is_inherited_by_a_division_run(tmp_path):
    seed(tmp_path)

    class Retriever:
        def search(self, query, faction_ids, top_k=5, source_id=None):
            return [{"relative_path": "forge/policy.md", "text": "Forge policy: never shell out.", "score": 0.9}]

    agent, llm = agent_with()
    report = agent.division("security").run(
        CodingRequest(task="Review for security problems", workspace=str(tmp_path)),
        CodingAgent.forge_invocation(Retriever()))
    assert report.context_strategy == "forge-rag"


def test_describe_lists_the_hierarchy():
    tree = CodingAgent.describe()
    assert tree["level"] == "agent" and tree["lead"] == "engineering_director"
    security = next(d for d in tree["divisions"] if d["id"] == "security")
    assert security["lead"] == "security_architect"
    assert {s["id"] for s in security["specialists"] if s["has_tools"]} == {"code_security_auditor",
                                                                          "dependency_auditor"}
