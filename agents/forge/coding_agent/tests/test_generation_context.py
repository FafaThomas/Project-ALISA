import pytest

from coding_agent.engineering_spec import (
    ArchitectureModule, EngineeringSpecification, ImplementationUnit,
    InterfaceContract, InterfaceField, Requirement,
)
from coding_agent.generation_context import (
    GenerationContextBuilder, GenerationContextError,
)


def make_spec():
    return EngineeringSpecification(
        specification_id="espec-context",
        task_id="task-context",
        objective="Build a task application",
        workspace="D:/workspace",
        requirements=(
            Requirement("REQ-1", "Create tasks", acceptance_criteria=("Task appears",)),
            Requirement("REQ-2", "Persist tasks"),
        ),
        architecture=(
            ArchitectureModule("MOD-STORE", "Store", "Owns task data", file_paths=("src/store.py",)),
            ArchitectureModule("MOD-UI", "UI", "Renders tasks", file_paths=("src/ui.py",)),
        ),
        contracts=(
            InterfaceContract(
                "CON-STORE", "MOD-STORE", "TaskStore", "function",
                inputs=(InterfaceField("task", "Task"),),
                outputs=(InterfaceField("id", "str"),),
                invariants=("IDs are unique",),
            ),
            InterfaceContract("CON-UI", "MOD-UI", "TaskView", "component"),
        ),
        implementation_units=(
            ImplementationUnit("store", "Implement store", "src/store.py",
                               satisfies=("REQ-1", "REQ-2"), implements=("CON-STORE",),
                               acceptance_criteria=("Supports create",)),
            ImplementationUnit("ui", "Implement UI", "src/ui.py",
                               depends_on=("store",), satisfies=("REQ-1",),
                               implements=("CON-UI",)),
        ),
    )


def test_builds_unit_scoped_context_with_contracts_and_requirements():
    context = GenerationContextBuilder().build(make_spec(), "store")
    assert context.unit["unit_id"] == "store"
    assert [x["requirement_id"] for x in context.requirements] == ["REQ-1", "REQ-2"]
    assert [x["contract_id"] for x in context.contracts] == ["CON-STORE"]
    assert [x["module_id"] for x in context.architecture_modules] == ["MOD-STORE"]
    assert context.acceptance_criteria == ("Supports create",)


def test_dependency_artifact_required_before_dependent_unit_generation():
    with pytest.raises(GenerationContextError, match="not ready"):
        GenerationContextBuilder().build(make_spec(), "ui")


def test_includes_direct_dependency_artifact_only():
    context = GenerationContextBuilder().build(
        make_spec(), "ui", dependency_artifacts={"store": "class TaskStore: pass"}
    )
    assert context.dependency_artifacts == ({
        "unit_id": "store", "target_path": "src/store.py",
        "content": "class TaskStore: pass",
    },)


def test_unknown_unit_is_rejected():
    with pytest.raises(GenerationContextError, match="unknown implementation unit"):
        GenerationContextBuilder().build(make_spec(), "missing")


def test_unknown_dependency_artifact_is_rejected():
    with pytest.raises(GenerationContextError, match="unknown units"):
        GenerationContextBuilder().build(
            make_spec(), "store", dependency_artifacts={"ghost": "x"}
        )


def test_project_files_are_sorted_and_bounded():
    builder = GenerationContextBuilder(max_file_chars=4, max_total_project_chars=6)
    context = builder.build(
        make_spec(), "store",
        project_files={"z.py": "123456", "a.py": "abcdef"},
    )
    assert context.project_files == (
        {"path": "a.py", "content": "abcd", "truncated": True},
        {"path": "z.py", "content": "12", "truncated": True},
    )


def test_unsafe_project_file_path_is_rejected():
    with pytest.raises(GenerationContextError, match="workspace-relative"):
        GenerationContextBuilder().build(
            make_spec(), "store", project_files={"../secret.py": "no"}
        )


def test_non_text_project_file_is_rejected():
    with pytest.raises(GenerationContextError, match="must be text"):
        GenerationContextBuilder().build(
            make_spec(), "store", project_files={"a.py": b"bytes"}
        )


def test_context_serializes_to_json():
    context = GenerationContextBuilder().build(make_spec(), "store")
    encoded = context.to_json()
    assert '"specification_id": "espec-context"' in encoded
    assert '"CON-STORE"' in encoded
