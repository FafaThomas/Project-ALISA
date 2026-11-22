import pytest

from coding_agent.artifact_validator import (
    ArtifactCheckStatus,
    GeneratedArtifactValidator,
)
from coding_agent.code_generator import ImplementationArtifact
from coding_agent.engineering_spec import (
    EngineeringSpecification, ImplementationUnit, InterfaceContract, InterfaceField,
)


def make_spec(path="src/widget.py", *, contracts=(), units=None):
    if units is None:
        units = (ImplementationUnit("widget", "Widget", path,
                                    implements=tuple(c.contract_id for c in contracts)),)
    return EngineeringSpecification(
        "spec", "task", "objective", "workspace",
        contracts=tuple(contracts), implementation_units=tuple(units),
    )


def statuses(report, check):
    return [f.status for f in report.findings if f.check == check]


def test_python_artifact_schema_and_syntax_pass():
    spec = make_spec()
    artifact = ImplementationArtifact("widget", "src/widget.py", "def widget():\n    return 1\n")
    report = GeneratedArtifactValidator().validate(spec, [artifact])
    assert ArtifactCheckStatus.PASSED in statuses(report, "artifact_schema")
    assert ArtifactCheckStatus.PASSED in statuses(report, "syntax")
    assert not report.failed


def test_python_syntax_error_fails():
    spec = make_spec()
    artifact = ImplementationArtifact("widget", "src/widget.py", "def widget(:\n")
    report = GeneratedArtifactValidator().validate(spec, [artifact])
    assert report.failed
    assert ArtifactCheckStatus.FAILED in statuses(report, "syntax")


@pytest.mark.parametrize("path", ["../escape.py", "/absolute.py", "C:\\temp\\x.py", ""])
def test_unsafe_artifact_path_fails(path):
    spec = make_spec()
    artifact = ImplementationArtifact("widget", path, "pass")
    report = GeneratedArtifactValidator().validate(spec, [artifact])
    assert report.failed
    assert ArtifactCheckStatus.FAILED in statuses(report, "path_safety")


def test_wrong_authorized_path_fails():
    spec = make_spec("src/widget.py")
    artifact = ImplementationArtifact("widget", "src/other.py", "pass")
    assert GeneratedArtifactValidator().validate(spec, [artifact]).failed


def test_unknown_unit_fails():
    spec = make_spec()
    artifact = ImplementationArtifact("missing", "src/widget.py", "pass")
    assert GeneratedArtifactValidator().validate(spec, [artifact]).failed


def test_duplicate_unit_artifact_fails():
    spec = make_spec()
    artifacts = [
        ImplementationArtifact("widget", "src/widget.py", "pass"),
        ImplementationArtifact("widget", "src/widget.py", "pass"),
    ]
    report = GeneratedArtifactValidator().validate(spec, artifacts)
    assert ArtifactCheckStatus.FAILED in statuses(report, "duplicate_unit")


def test_duplicate_target_path_fails_across_units():
    units = (
        ImplementationUnit("one", "One", "shared.py"),
        ImplementationUnit("two", "Two", "shared.py"),
    )
    spec = make_spec(units=units)
    artifacts = [ImplementationArtifact("one", "shared.py", "x=1"),
                 ImplementationArtifact("two", "shared.py", "x=2")]
    report = GeneratedArtifactValidator().validate(spec, artifacts)
    assert ArtifactCheckStatus.FAILED in statuses(report, "path_conflict")


def test_json_syntax_is_validated():
    spec = make_spec("config.json")
    report = GeneratedArtifactValidator().validate(
        spec, [ImplementationArtifact("widget", "config.json", '{"ok": true}')])
    assert ArtifactCheckStatus.PASSED in statuses(report, "syntax")


def test_invalid_json_fails():
    spec = make_spec("config.json")
    report = GeneratedArtifactValidator().validate(
        spec, [ImplementationArtifact("widget", "config.json", '{bad}')])
    assert ArtifactCheckStatus.FAILED in statuses(report, "syntax")


def test_unsupported_syntax_is_unverified_not_passed():
    spec = make_spec("src/widget.jsx")
    report = GeneratedArtifactValidator().validate(
        spec, [ImplementationArtifact("widget", "src/widget.jsx", "export default function X() {}")])
    assert report.unverified
    assert not report.accepted


def test_js_local_import_missing_from_inventory_fails():
    spec = make_spec("src/widget.jsx")
    source = "import Thing from './Thing'; export default Thing;"
    report = GeneratedArtifactValidator().validate(
        spec, [ImplementationArtifact("widget", "src/widget.jsx", source)],
        project_files={"src/Other.jsx": "export default 1"},
    )
    assert ArtifactCheckStatus.FAILED in statuses(report, "import_consistency")


def test_js_local_import_resolves_in_inventory():
    spec = make_spec("src/widget.jsx")
    source = "import Thing from './Thing'; export default Thing;"
    report = GeneratedArtifactValidator().validate(
        spec, [ImplementationArtifact("widget", "src/widget.jsx", source)],
        project_files={"src/Thing.jsx": "export default 1"},
    )
    assert ArtifactCheckStatus.FAILED not in statuses(report, "import_consistency")


def test_required_contract_is_checked():
    contract = InterfaceContract(
        "widget-contract", "ui", "Widget", "component",
        inputs=(InterfaceField("task", "Task"), InterfaceField("onDelete", "Function")),
    )
    spec = make_spec("src/widget.jsx", contracts=(contract,))
    artifact = ImplementationArtifact("widget", "src/widget.jsx",
        "function Widget({ task }) { return null; }")
    report = GeneratedArtifactValidator().validate(spec, [artifact])
    assert ArtifactCheckStatus.FAILED in statuses(report, "contract_compliance")


def test_empty_artifact_batch_is_unverified():
    spec = make_spec()
    report = GeneratedArtifactValidator().validate(spec, [])
    assert report.unverified
    assert not report.accepted
