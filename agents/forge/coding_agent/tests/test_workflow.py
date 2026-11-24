import json

from conftest import CALC, MAIN, ScriptedLLM, calculator_handlers

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core.llm import ModelRouter

UI = InvocationContext(source=InvocationSource.UI)
TASK = "Build a calculator CLI in Python with add and divide"


def run(tmp_path, llm, **kw):
    agent = CodingAgent.with_client(llm)
    events = []
    report = agent.run(CodingRequest(task=TASK, workspace=str(tmp_path), **kw), UI, emit=events.append)
    return report, events


def test_end_to_end_team_writes_files(tmp_path):
    llm = ScriptedLLM(calculator_handlers())
    report, events = run(tmp_path, llm)

    assert report.status == "completed", report.error
    assert report.context_strategy == "markdown"
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == CALC
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == MAIN
    assert report.verification.passed
    # phase order: director -> architect -> tech lead -> developer (x2)
    assert llm.roles_called() == ["engineering_director", "software_architect", "tech_lead",
                                  "python_developer", "python_developer"]
    run_dir = tmp_path / ".coding-agent" / "runs" / report.run_id
    saved = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["status"] == "completed" and "content" not in saved["changes"][0]
    assert "Software Architect" in (run_dir / "handoff.md").read_text(encoding="utf-8")
    memory = (tmp_path / ".coding-agent" / "memory" / "PROJECT_MEMORY.md").read_text(encoding="utf-8")
    assert "calculator.py" in memory and "software_architect decision" in memory
    assert events[0]["event"] == "run_started" and events[-1]["status"] == "completed"


def test_builders_see_team_decisions_and_teammates_work(tmp_path):
    llm = ScriptedLLM(calculator_handlers())
    run(tmp_path, llm)
    second_build = [c for c in llm.calls if c["role"] == "python_developer"][1]["prompt"]
    assert "software_architect decision" in second_build
    assert "W1 (Python Developer): Calculator core [calculator.py]" in second_build
    assert "- calculator.py" in second_build  # already produced this run


def test_propose_mode_does_not_touch_the_project(tmp_path):
    report, _ = run(tmp_path, ScriptedLLM(calculator_handlers()), mode="propose")
    assert report.status == "proposed"
    assert not (tmp_path / "calculator.py").exists()
    assert (tmp_path / ".coding-agent" / "runs" / report.run_id / "proposed" / "calculator.py").exists()


def test_apply_proposal_after_approval(tmp_path):
    agent = CodingAgent.with_client(ScriptedLLM(calculator_handlers()))
    report = agent.run(CodingRequest(task=TASK, workspace=str(tmp_path), mode="propose"), UI)
    agent.apply_proposal(report, UI)
    assert report.status == "completed"
    assert (tmp_path / "main.py").exists()


def test_syntax_error_is_repaired_by_the_debugging_specialist(tmp_path):
    handlers = calculator_handlers()
    builder = handlers["python_developer"]
    handlers["python_developer"] = lambda p, s: (
        {"summary": "oops", "files": [{"path": "calculator.py", "action": "create", "content": "def add(a, b)\n"}]}
        if "Your work item W1" in p else builder(p, s)
    )
    handlers["debugging_specialist"] = lambda p, s: {
        "summary": "added missing colon",
        "files": [{"path": "calculator.py", "action": "modify", "content": CALC}],
    }
    llm = ScriptedLLM(handlers)
    report, _ = run(tmp_path, llm)
    assert report.status == "completed"
    repair_prompt = [c for c in llm.calls if c["role"] == "debugging_specialist"][0]["prompt"]
    assert "calculator.py [python-compile]" in repair_prompt and "def add(a, b)" in repair_prompt
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == CALC


def test_unrepairable_output_is_not_written(tmp_path):
    handlers = calculator_handlers()
    handlers["python_developer"] = {"summary": "bad", "files": [
        {"path": "calculator.py", "action": "create", "content": "def broken(:\n"}]}
    llm = ScriptedLLM(handlers)
    report, _ = run(tmp_path, llm)
    assert report.status == "failed"
    assert "nothing was written" in report.error
    assert llm.roles_called().count("debugging_specialist") == 2
    assert not (tmp_path / "calculator.py").exists()
    assert (tmp_path / ".coding-agent" / "runs" / report.run_id / "proposed" / "calculator.py").exists()


def test_unsafe_paths_from_builders_are_rejected(tmp_path):
    handlers = calculator_handlers()
    builder = handlers["python_developer"]

    def sneaky(prompt, schema):
        reply = builder(prompt, schema)
        reply["files"].append({"path": "../escape.py", "action": "create", "content": "x = 1\n"})
        return reply
    handlers["python_developer"] = sneaky
    report, _ = run(tmp_path, ScriptedLLM(handlers))
    assert report.status == "completed"
    assert not (tmp_path.parent / "escape.py").exists()
    assert any("rejected file" in c for o in report.outputs for c in o.concerns)


def test_reviewers_are_advisory_and_fix_round_is_opt_in(tmp_path):
    handlers = calculator_handlers()
    handlers["engineering_director"] = {"rationale": "x", "team": ["python_developer", "code_security_auditor"]}
    handlers["code_security_auditor"] = {"summary": "one issue", "findings": [
        {"severity": "high", "file": "main.py", "issue": "prints debug output", "suggestion": "use logging"}]}
    handlers["debugging_specialist"] = {"summary": "fixed", "files": [
        {"path": "main.py", "action": "modify", "content": MAIN.replace("print", "repr")}]}

    llm = ScriptedLLM(handlers)
    report, _ = run(tmp_path / "a", llm, acceptance_criteria=["divide by zero raises"])
    assert report.status == "completed"
    assert [f.issue for f in report.advisory_findings] == ["prints debug output"]
    assert "debugging_specialist" not in llm.roles_called()

    llm = ScriptedLLM(handlers)
    report, _ = run(tmp_path / "b", llm, fix_review_findings=True)
    assert "debugging_specialist" in llm.roles_called()
    assert "repr(add" in (tmp_path / "b" / "main.py").read_text(encoding="utf-8")


def test_unusable_tech_lead_plan_falls_back_to_one_work_item(tmp_path):
    handlers = calculator_handlers()
    handlers["tech_lead"] = {"summary": "??", "work_items": "not a list"}
    handlers["python_developer"] = {"summary": "all", "files": [
        {"path": "calculator.py", "action": "create", "content": CALC}]}
    report, _ = run(tmp_path, ScriptedLLM(handlers))
    assert report.status == "completed"
    assert (tmp_path / "calculator.py").exists()


def test_forge_invocation_uses_rag_and_api_invocation_does_not(tmp_path):
    class Retriever:
        calls = 0

        def search(self, query, faction_ids, top_k=5, source_id=None):
            Retriever.calls += 1
            return [{"relative_path": "forge/notes.md", "text": "Forge says: prefer Decimal.", "score": 0.8}]

    llm = ScriptedLLM(calculator_handlers())
    agent = CodingAgent.with_client(llm)
    report = agent.run(CodingRequest(task=TASK, workspace=str(tmp_path / "f")),
                       CodingAgent.forge_invocation(Retriever()))
    assert report.context_strategy == "forge-rag" and report.invocation_source == "forge"
    assert Retriever.calls > 0
    assert any("Forge says: prefer Decimal." in c["prompt"] for c in llm.calls)

    llm = ScriptedLLM(calculator_handlers())
    report = CodingAgent.with_client(llm).run(
        CodingRequest(task=TASK, workspace=str(tmp_path / "a")), InvocationContext(source=InvocationSource.API))
    assert report.context_strategy == "markdown"
    assert not any("Forge says" in c["prompt"] for c in llm.calls)


def test_follow_up_run_reads_existing_workspace_first(tmp_path):
    run(tmp_path, ScriptedLLM(calculator_handlers()))
    llm = ScriptedLLM(calculator_handlers())
    agent = CodingAgent.with_client(llm, use_director_llm=False)
    agent.run(CodingRequest(task="Add a multiply function to the calculator", workspace=str(tmp_path)), UI)
    assert llm.roles_called()[0] == "codebase_analyst"
    analyst_prompt = llm.calls[0]["prompt"]
    assert "Decisions from earlier runs" in analyst_prompt and "calculator.py" in analyst_prompt


def test_model_router_unloads_when_tier_model_changes(tmp_path):
    planning, coding = ScriptedLLM(calculator_handlers(), "planner"), ScriptedLLM(calculator_handlers(), "coder")
    router = ModelRouter({"planning": planning, "review": planning, "coding": coding})
    report = CodingAgent(router).run(CodingRequest(task=TASK, workspace=str(tmp_path)), UI)
    assert report.status == "completed"
    assert set(planning.roles_called()) == {"engineering_director", "software_architect", "tech_lead"}
    assert set(coding.roles_called()) == {"python_developer"}
    assert planning.unloads == 1  # planning -> coding swap, once


def test_llm_outage_is_reported_not_raised(tmp_path):
    class Down:
        model = "down"

        def generate_json(self, *a, **k):
            from coding_agent.core.llm import LLMError
            raise LLMError("connection refused")

        def unload(self):
            return True

    report = CodingAgent.with_client(Down()).run(CodingRequest(task=TASK, workspace=str(tmp_path)), UI)
    assert report.status == "failed"
    assert report.error
    assert (tmp_path / ".coding-agent" / "runs" / report.run_id / "report.json").exists()
