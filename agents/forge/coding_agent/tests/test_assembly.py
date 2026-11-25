import pytest

from coding_agent.core.llm import LLMError, ModelRouter
from coding_agent.core.models import CodingRequest
from coding_agent.core.selection import TeamAssembler, score_roles


def heuristic(task, files=(), **kw):
    request = CodingRequest(task=task, workspace="w", **kw)
    return TeamAssembler(None, use_director_llm=False).heuristic(request, list(files))


def test_small_python_task_gets_a_small_team():
    team = heuristic("Build a calculator CLI in Python with add and divide")
    assert team.ids == ["engineering_director", "software_architect", "python_developer", "tech_lead"]
    assert [r.id for r in team.standby] == ["debugging_specialist"]


def test_cross_stack_request_covers_each_layer():
    team = heuristic("Create a React dashboard with a FastAPI backend and a Postgres database, "
                     "dockerize it and add unit tests")
    for role in ("frontend_developer", "python_developer", "database_developer",
                 "unit_testing_engineer", "docker_engineer", "database_architect"):
        assert role in team.ids
    assert "backend_developer" not in team.ids  # subsumed by the Python developer
    assert len(team.ids) <= 10


def test_existing_code_brings_the_codebase_analyst_and_language_developer():
    team = heuristic("Fix the bug where retrieval crashes on empty collections",
                     files=["src/retrieval.py", "src/cli.py", "README.md"])
    assert team.roles[0].id == "codebase_analyst"
    assert "debugging_specialist" in team.ids and "python_developer" in team.ids


def test_trigger_matching_uses_word_boundaries():
    scores = score_roles("the city should apply tags to images")
    assert "devops_engineer" not in scores  # "ci" inside "city"
    assert "docker_engineer" not in scores and "release_engineer" not in scores
    assert "systems_integration_engineer" in score_roles("integrate with Stripe")


def test_pentest_needs_an_explicit_request():
    assert "penetration_testing_agent" not in heuristic("Add a login form with security review").ids
    assert "penetration_testing_agent" in heuristic("Run an authorized pentest review of the login code").ids


def test_requested_roles_are_validated_and_kept():
    team = heuristic("Build a calculator in Python", requested_roles=["technical_writer"])
    assert "technical_writer" in team.ids
    with pytest.raises(ValueError, match="unknown requested roles"):
        heuristic("x", requested_roles=["wizard"])


def test_max_team_size_trims_least_essential_first():
    team = heuristic("Create a React dashboard with a FastAPI backend and a Postgres database, "
                     "dockerize it and add unit tests", max_team_size=5)
    assert len(team.ids) == 5
    assert {"engineering_director", "tech_lead"} <= set(team.ids)
    assert any(r.kind == "builder" for r in team.roles)


def test_director_refinement_is_validated(scripted):
    llm = scripted({"engineering_director": {
        "rationale": "lean", "team": ["python_developer", "wizard", "penetration_testing_agent", "qa_lead"],
    }})
    request = CodingRequest(task="Build a calculator CLI in Python", workspace="w")
    team = TeamAssembler(ModelRouter.single(llm)).assemble(request, [])
    assert team.ids == ["engineering_director", "python_developer", "tech_lead", "qa_lead"]
    assert team.rationale == "lean"


def test_director_must_leave_a_developer(scripted):
    llm = scripted({"engineering_director": {"rationale": "x", "team": ["qa_lead"]}})
    request = CodingRequest(task="Build a calculator CLI in Python", workspace="w")
    team = TeamAssembler(ModelRouter.single(llm)).assemble(request, [])
    assert "python_developer" in team.ids


def test_director_failure_falls_back_to_heuristic(scripted):
    def boom(prompt, schema):
        raise LLMError("offline")
    llm = scripted({"engineering_director": boom})
    request = CodingRequest(task="Build a calculator CLI in Python", workspace="w")
    team = TeamAssembler(ModelRouter.single(llm)).assemble(request, [])
    assert team.rationale.startswith("Keyword and workspace match")


def test_director_cannot_add_test_writers_the_request_did_not_ask_for(scripted):
    llm = scripted({"engineering_director": {"rationale": "add testing", "team": [
        "python_developer", "unit_testing_engineer", "integration_testing_engineer"]}})
    request = CodingRequest(task="Build a calculator CLI in Python", workspace="w")
    team = TeamAssembler(ModelRouter.single(llm)).assemble(request, [])
    assert "unit_testing_engineer" not in team.ids and "integration_testing_engineer" not in team.ids
    asked = CodingRequest(task="Build a calculator CLI in Python with unit tests", workspace="w")
    assert "unit_testing_engineer" in TeamAssembler(ModelRouter.single(llm)).assemble(asked, []).ids
