import json
import pytest

from coding_agent.engineering_spec import (
    ArchitectureModule, EngineeringSpecification, ImplementationUnit,
    InterfaceContract, InterfaceField, Requirement, RequirementKind,
    SpecificationStage, UnitKind, VerificationCriterion, VerificationKind,
)


def make_spec(**overrides):
    values = dict(
        specification_id="espec-task-1", task_id="task-1",
        objective="Build a task dashboard", workspace="D:/workspace",
        requirements=(Requirement("REQ-1", "Users can add tasks", RequirementKind.FUNCTIONAL, ("A task appears after submission",)),),
        architecture=(ArchitectureModule("ui", "UI", "Render the application"),),
        contracts=(InterfaceContract("CON-1", "TaskForm", "onAddTask", "callback", inputs=(InterfaceField("task", "Task"),)),),
        implementation_units=(ImplementationUnit("form", "Task form", "src/TaskForm.jsx", satisfies=("REQ-1",), implements=("CON-1",)),),
        verification=(VerificationCriterion("VER-1", "Task creation works", VerificationKind.BEHAVIORAL, covers=("REQ-1", "form")),),
    )
    values.update(overrides)
    return EngineeringSpecification(**values)


def test_requirement_exposes_stable_acceptance_criterion_ids():
    requirement = Requirement(
        "REQ-LOGIN",
        "Users can sign in",
        acceptance_criteria=("A valid user can sign in", "Invalid credentials are rejected"),
    )
    reordered = Requirement(
        "REQ-LOGIN",
        "Users can sign in",
        acceptance_criteria=("Invalid credentials are rejected", "A valid user can sign in"),
    )

    assert len(requirement.acceptance_criterion_ids) == 2
    assert requirement.acceptance_criterion_ids[0] != requirement.acceptance_criterion_ids[1]
    assert requirement.acceptance_criterion_ids == (
        reordered.acceptance_criterion_ids[1],
        reordered.acceptance_criterion_ids[0],
    )


def test_spec_defaults_to_task_scoped_hybrid_stages_and_sandbox():
    spec = make_spec()
    assert spec.execution_profile == "sandbox"
    assert spec.generation_stages == (
        SpecificationStage.REQUIREMENTS, SpecificationStage.ARCHITECTURE,
        SpecificationStage.CONTRACTS, SpecificationStage.INTEGRATION,
    )
    assert spec.status.value == "draft"


def test_spec_serializes_to_json_compatible_data():
    spec = make_spec()
    payload = json.loads(spec.to_json())
    assert payload["task_id"] == "task-1"
    assert payload["requirements"][0]["kind"] == "functional"
    assert payload["execution_profile"] == "sandbox"


def test_implementation_unit_rejects_unsafe_paths():
    with pytest.raises(ValueError, match="workspace-relative"):
        ImplementationUnit("bad", "Bad", "../outside.py")


def test_spec_rejects_unknown_requirement_reference():
    with pytest.raises(ValueError, match="unknown requirements"):
        make_spec(implementation_units=(ImplementationUnit("form", "Form", "src/Form.jsx", satisfies=("MISSING",)),))


def test_spec_rejects_unknown_contract_reference():
    with pytest.raises(ValueError, match="unknown contracts"):
        make_spec(implementation_units=(ImplementationUnit("form", "Form", "src/Form.jsx", implements=("MISSING",)),))


def test_spec_rejects_unknown_verification_coverage():
    with pytest.raises(ValueError, match="covers unknown"):
        make_spec(verification=(VerificationCriterion("VER", "Check", VerificationKind.BUILD, covers=("MISSING",)),))


def test_spec_rejects_cyclic_implementation_dependencies():
    units = (
        ImplementationUnit("a", "A", "src/a.py", depends_on=("b",)),
        ImplementationUnit("b", "B", "src/b.py", depends_on=("a",)),
    )
    with pytest.raises(ValueError, match="cycle"):
        make_spec(implementation_units=units, verification=())


def test_spec_rejects_cyclic_architecture_dependencies():
    modules = (
        ArchitectureModule("a", "A", "A", depends_on=("b",)),
        ArchitectureModule("b", "B", "B", depends_on=("a",)),
    )
    with pytest.raises(ValueError, match="cycle"):
        make_spec(architecture=modules, verification=())


def test_spec_rejects_non_sandbox_execution_profile():
    with pytest.raises(ValueError, match="sandbox"):
        make_spec(execution_profile="workspace")


def test_contract_rejects_duplicate_input_names():
    with pytest.raises(ValueError, match="unique"):
        InterfaceContract("C", "X", "fn", "function", inputs=(InterfaceField("x", "str"), InterfaceField("x", "int")))
