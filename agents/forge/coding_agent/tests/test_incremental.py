from types import SimpleNamespace

import pytest

from coding_agent.artifact_pipeline import PreparedArtifactBatch
from coding_agent.code_generator import ImplementationArtifact
from coding_agent.code_index import CodeIndex, ModuleRecord
from coding_agent.engineering_spec import EngineeringSpecification, ImplementationUnit
from coding_agent.incremental import (
    ChangeImpactAnalyzer,
    IncrementalAnalysisError,
    IncrementalPlanBuilder,
    IncrementalUnitSelector,
)
from coding_agent.models import ChangeType, ImplementationPlan
from coding_agent.resolvers.models import Relationship
from coding_agent.workspace import Workspace


def make_spec(root):
    return EngineeringSpecification(
        specification_id="spec",
        task_id="task",
        objective="incremental test",
        workspace=str(root),
        implementation_units=(
            ImplementationUnit("db", "Database", "src/db.py"),
            ImplementationUnit("repo", "Repository", "src/repo.py", depends_on=("db",)),
            ImplementationUnit("service", "Service", "src/service.py", depends_on=("repo",)),
            ImplementationUnit("api", "API", "src/api.py", depends_on=("service",)),
            ImplementationUnit("ui", "UI", "src/ui.py"),
        ),
    )


def make_index():
    paths = ("src/db.py", "src/repo.py", "src/service.py", "src/api.py", "src/ui.py")
    modules = tuple(
        ModuleRecord(path, (path.rsplit("/", 1)[-1][:-3],), (), (), "python")
        for path in paths
    )
    relationships = (
        Relationship("imports", "src/repo.py", "repo", None, "src/db.py", "db", None, 1),
        Relationship("imports", "src/service.py", "service", None, "src/repo.py", "repo", None, 1),
        Relationship("imports", "src/api.py", "api", None, "src/service.py", "service", None, 1),
        Relationship("imports", "src/ui.py", "ui", None, None, None, "external_lib", 1, status="unresolved"),
    )
    return CodeIndex(modules, (), len(paths), len(paths), 0, relationships)


def test_impact_analysis_finds_transitive_dependents_and_is_deterministic():
    report = ChangeImpactAnalyzer().analyze(make_index(), ["src/db.py"])
    assert report.changed_paths == ("src/db.py",)
    assert report.affected_paths == (
        "src/api.py", "src/db.py", "src/repo.py", "src/service.py"
    )
    assert report.dependent_paths == ("src/api.py", "src/repo.py", "src/service.py")
    assert report.complete is True


def test_impact_analysis_reports_unindexed_changed_paths():
    report = ChangeImpactAnalyzer().analyze(make_index(), ["new/file.py"])
    assert report.unindexed_changed_paths == ("new/file.py",)
    assert report.complete is False
    assert report.affected_paths == ("new/file.py",)


@pytest.mark.parametrize("path", ["../outside.py", "/etc/passwd", "C:\\outside.py", ""])
def test_impact_analysis_rejects_unsafe_paths(path):
    with pytest.raises(IncrementalAnalysisError):
        ChangeImpactAnalyzer().analyze(make_index(), [path])


def test_selector_maps_paths_and_includes_downstream_units(tmp_path):
    impact = ChangeImpactAnalyzer().analyze(make_index(), ["src/db.py"])
    selection = IncrementalUnitSelector().select(make_spec(tmp_path), impact)
    assert selection.directly_affected_unit_ids == ("db", "repo", "service", "api")
    assert selection.selected_unit_ids == ("db", "repo", "service", "api")
    assert selection.omitted_unit_ids == ("ui",)
    assert selection.unmatched_affected_paths == ()


def test_incremental_plan_contains_only_selected_validated_artifacts(tmp_path):
    spec = make_spec(tmp_path)
    artifacts = tuple(
        ImplementationArtifact(unit.unit_id, unit.target_path, "VALUE = 1")
        for unit in spec.implementation_units
    )
    original_plan = ImplementationPlan(
        task_id="task", summary="full", changes=tuple(), requires_approval=True
    )
    batch = PreparedArtifactBatch(
        task_id="task", artifacts=artifacts,
        validation=SimpleNamespace(accepted=True),
        plan=original_plan, policy_accepted=True,
    )
    impact = ChangeImpactAnalyzer().analyze(make_index(), ["src/db.py"])
    selection = IncrementalUnitSelector().select(spec, impact)
    result = IncrementalPlanBuilder().build(
        Workspace(tmp_path), batch, impact, selection
    )
    assert [item.unit_id for item in result.artifacts] == ["db", "repo", "service", "api"]
    assert [change.path for change in result.plan.changes] == [
        "src/db.py", "src/repo.py", "src/service.py", "src/api.py"
    ]
    assert all(change.change_type == ChangeType.CREATE for change in result.plan.changes)
    assert result.plan.requires_approval is True
    assert not (tmp_path / "src").exists()


def test_incremental_plan_rejects_batch_that_is_not_approval_ready(tmp_path):
    batch = PreparedArtifactBatch(
        task_id="task", artifacts=(), validation=SimpleNamespace(accepted=False),
        plan=None, policy_accepted=False,
    )
    impact = ChangeImpactAnalyzer().analyze(make_index(), ["src/db.py"])
    selection = IncrementalUnitSelector().select(make_spec(tmp_path), impact)
    with pytest.raises(IncrementalAnalysisError, match="accepted for approval"):
        IncrementalPlanBuilder().build(Workspace(tmp_path), batch, impact, selection)



def test_impact_analysis_preserves_uncertain_relationships_in_affected_modules():
    index = make_index()
    uncertain = Relationship(
        "imports", "src/api.py", "api", None, None, None,
        "legacy_adapter", 9, status="ambiguous",
        candidates=("src/legacy_a.py", "src/legacy_b.py"),
    )
    index = CodeIndex(
        index.modules, index.dependencies, index.discovered_files,
        index.indexed_files, index.failed_files,
        index.relationships + (uncertain,),
    )
    report = ChangeImpactAnalyzer().analyze(index, ["src/db.py"])
    assert report.has_uncertainty is True
    assert report.complete is False
    assert report.ambiguous_relationships == (uncertain,)


def test_impact_analysis_rejects_empty_changed_path_list():
    with pytest.raises(IncrementalAnalysisError, match="at least one"):
        ChangeImpactAnalyzer().analyze(make_index(), [])
