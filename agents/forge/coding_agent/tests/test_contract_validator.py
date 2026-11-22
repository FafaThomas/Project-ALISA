import pytest

from coding_agent.code_generator import ImplementationArtifact
from coding_agent.contract_validator import ContractComplianceValidator, ContractCheckStatus
from coding_agent.engineering_spec import (
    EngineeringSpecification, ImplementationUnit, InterfaceContract, InterfaceField,
)


def make_spec(path="src/widget.jsx", contract_name="Widget", inputs=("task", "onDelete")):
    contract = InterfaceContract(
        contract_id="widget-contract", owner="ui", name=contract_name, kind="component",
        inputs=tuple(InterfaceField(name, "unknown") for name in inputs),
    )
    unit = ImplementationUnit(
        unit_id="widget", title="Widget", target_path=path, implements=("widget-contract",),
    )
    return EngineeringSpecification(
        specification_id="spec", task_id="task", objective="test", workspace="workspace",
        contracts=(contract,), implementation_units=(unit,),
    )


def test_jsx_component_contract_passes():
    spec = make_spec()
    artifact = ImplementationArtifact("widget", "src/widget.jsx", "function Widget({ task, onDelete }) { return null; }")
    report = ContractComplianceValidator().validate(spec, artifact)
    assert report.findings[0].status == ContractCheckStatus.PASSED
    assert report.passed


def test_jsx_missing_required_input_fails():
    spec = make_spec()
    artifact = ImplementationArtifact("widget", "src/widget.jsx", "function Widget({ task }) { return null; }")
    report = ContractComplianceValidator().validate(spec, artifact)
    assert report.failed
    assert report.findings[0].missing_inputs == ("onDelete",)


def test_jsx_arrow_component_is_supported():
    spec = make_spec()
    artifact = ImplementationArtifact("widget", "src/widget.jsx", "const Widget = ({ task, onDelete }) => null;")
    assert ContractComplianceValidator().validate(spec, artifact).passed


def test_python_function_contract_passes():
    spec = make_spec("widget.py", "create_widget", ("task", "on_delete"))
    artifact = ImplementationArtifact("widget", "widget.py", "def create_widget(task, on_delete):\n    return task\n")
    assert ContractComplianceValidator().validate(spec, artifact).passed


def test_python_missing_input_fails():
    spec = make_spec("widget.py", "create_widget", ("task", "on_delete"))
    artifact = ImplementationArtifact("widget", "widget.py", "def create_widget(task):\n    return task\n")
    report = ContractComplianceValidator().validate(spec, artifact)
    assert report.failed
    assert report.findings[0].missing_inputs == ("on_delete",)


def test_python_syntax_error_fails():
    spec = make_spec("widget.py", "create_widget", ())
    artifact = ImplementationArtifact("widget", "widget.py", "def create_widget(:\n")
    assert ContractComplianceValidator().validate(spec, artifact).failed


def test_unsupported_language_is_unverified_not_passed():
    spec = make_spec("widget.rs")
    artifact = ImplementationArtifact("widget", "widget.rs", "fn Widget() {}")
    report = ContractComplianceValidator().validate(spec, artifact)
    assert report.unverified
    assert not report.passed


def test_wrong_artifact_path_is_rejected():
    spec = make_spec()
    artifact = ImplementationArtifact("widget", "other.jsx", "function Widget({ task, onDelete }) {}")
    with pytest.raises(ValueError, match="authorized"):
        ContractComplianceValidator().validate(spec, artifact)


def test_unknown_unit_is_rejected():
    spec = make_spec()
    artifact = ImplementationArtifact("missing", "src/widget.jsx", "x")
    with pytest.raises(ValueError, match="unknown implementation unit"):
        ContractComplianceValidator().validate(spec, artifact)


def test_no_contracts_is_empty_successful_report():
    spec = EngineeringSpecification(
        specification_id="spec", task_id="task", objective="test", workspace="workspace",
        implementation_units=(ImplementationUnit("plain", "Plain", "plain.txt"),),
    )
    artifact = ImplementationArtifact("plain", "plain.txt", "hello")
    report = ContractComplianceValidator().validate(spec, artifact)
    assert report.findings == ()
    assert report.passed
