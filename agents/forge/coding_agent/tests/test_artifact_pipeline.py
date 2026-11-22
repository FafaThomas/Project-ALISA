from pathlib import Path

import pytest

from coding_agent.artifact_pipeline import ArtifactGenerationPipeline, ArtifactPipelineError
from coding_agent.artifact_validator import ArtifactCheckStatus, GeneratedArtifactValidator
from coding_agent.code_generator import ImplementationArtifact
from coding_agent.engineering_spec import EngineeringSpecification, ImplementationUnit
from coding_agent.models import ChangeType
from coding_agent.workspace import Workspace


class FakeGenerator:
    def __init__(self, contents=None):
        self.contents = contents or {}
        self.calls = []

    def generate(self, specification, unit_id, *, dependency_artifacts=None, project_files=None):
        self.calls.append((unit_id, dict(dependency_artifacts or {}), dict(project_files or {})))
        unit = next(item for item in specification.implementation_units if item.unit_id == unit_id)
        return ImplementationArtifact(unit_id, unit.target_path, self.contents.get(unit_id, '{"ok": true}'))


def make_spec(root, units=None):
    return EngineeringSpecification(
        specification_id="spec-1",
        task_id="task-1",
        objective="Build a small application",
        workspace=str(root),
        implementation_units=tuple(units or [
            ImplementationUnit("base", "Base config", "base.json"),
            ImplementationUnit("app", "Application", "app.json", depends_on=("base",)),
        ]),
    )


def test_pipeline_generates_in_dependency_order_and_prepares_plan(tmp_path):
    generator = FakeGenerator()
    pipeline = ArtifactGenerationPipeline(generator, Workspace(tmp_path))

    result = pipeline.prepare(make_spec(tmp_path), verification_commands=(("python", "-m", "pytest"),))

    assert generator.calls[0][0] == "base"
    assert generator.calls[1][0] == "app"
    assert generator.calls[1][1] == {"base": '{"ok": true}'}
    assert result.ready_for_approval is True
    assert result.validation.accepted is True
    assert [change.path for change in result.plan.changes] == ["base.json", "app.json"]
    assert all(change.change_type == ChangeType.CREATE for change in result.plan.changes)
    assert result.plan.requires_approval is True
    assert not (tmp_path / "base.json").exists()
    assert not (tmp_path / "app.json").exists()


def test_failed_artifact_validation_blocks_plan(tmp_path):
    generator = FakeGenerator({"base": '{ invalid json'})
    pipeline = ArtifactGenerationPipeline(generator, Workspace(tmp_path))

    result = pipeline.prepare(make_spec(tmp_path))

    assert result.validation.failed is True
    assert result.plan is None
    assert result.ready_for_approval is False
    assert not (tmp_path / "base.json").exists()


def test_unverified_checks_block_by_default_but_can_be_explicitly_allowed(tmp_path):
    spec = make_spec(tmp_path, [ImplementationUnit("module", "Python module", "module.py")])
    generator = FakeGenerator({"module": "VALUE = 1\n"})
    pipeline = ArtifactGenerationPipeline(generator, Workspace(tmp_path))

    blocked = pipeline.prepare(spec)
    assert blocked.validation.unverified is True
    assert blocked.plan is None

    allowed = pipeline.prepare(spec, allow_unverified=True)
    assert allowed.plan is not None
    assert allowed.validation.unverified is True
    assert allowed.ready_for_approval is True


def test_existing_target_becomes_modify(tmp_path):
    target = tmp_path / "base.json"
    target.write_text('{"old": true}', encoding="utf-8")
    spec = make_spec(tmp_path, [ImplementationUnit("base", "Base config", "base.json")])
    pipeline = ArtifactGenerationPipeline(FakeGenerator(), Workspace(tmp_path))

    result = pipeline.prepare(spec)

    assert result.ready_for_approval is True
    assert result.plan.changes[0].change_type == ChangeType.MODIFY


def test_workspace_mismatch_is_rejected(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    pipeline = ArtifactGenerationPipeline(FakeGenerator(), Workspace(tmp_path))

    with pytest.raises(ArtifactPipelineError, match="does not match"):
        pipeline.prepare(make_spec(other))
