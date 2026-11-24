import pytest

from coding_agent.specification_checks import (
    CheckFailure,
    readme_missing_topics,
    run_checks,
)


def _payload(criteria, description="Create README.md with project documentation"):
    return {
        "requirements": [{
            "requirement_id": "REQ-005",
            "description": description,
            "kind": "functional",
            "acceptance_criteria": list(criteria),
        }],
        "units": [{"unit_id": "UNIT-005", "target_path": "README.md", "acceptance_criteria": list(criteria)}],
    }


def test_plain_project_readme_passes_without_docker_sections(tmp_path):
    # Regression: a Pygame project was failing for lacking Docker/Compose coverage.
    (tmp_path / "README.md").write_text(
        "# Snake\n\nRun the game with `python main.py`.\n", encoding="utf-8"
    )

    findings = run_checks(tmp_path, _payload(["README.md explains project structure and usage"]))

    assert any("README.md" in finding for finding in findings)


def test_readme_must_be_non_empty(tmp_path):
    (tmp_path / "README.md").write_text("   \n", encoding="utf-8")

    with pytest.raises(CheckFailure, match="is empty"):
        run_checks(tmp_path, _payload(["README.md explains project structure and usage"]))


def test_prerequisites_required_only_when_the_spec_asks_for_them(tmp_path):
    (tmp_path / "README.md").write_text("# App\n\nUsage notes.\n", encoding="utf-8")

    with pytest.raises(CheckFailure, match="prerequisites"):
        run_checks(tmp_path, _payload(["README.md lists prerequisites for the project"]))


def test_compose_workflow_required_only_when_the_spec_asks_for_it():
    readme = "# App\n\nRun it with the usual commands.\n"

    assert readme_missing_topics(readme, "Create README.md documenting the project") == []
    assert readme_missing_topics(readme, "README documents the docker compose workflow") == ["compose workflow"]


def test_triggered_topic_is_satisfied_by_matching_readme_content():
    readme = "## Prerequisites\n\nPython 3.12 and pip.\n"

    assert readme_missing_topics(readme, "README lists prerequisites") == []
