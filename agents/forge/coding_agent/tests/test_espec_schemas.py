"""Tests for the Pydantic boundary contracts used by staged ESpec generation."""

import pytest
from pydantic import ValidationError

from coding_agent.schemas.espec import (
    ArchitectureStagePayload,
    ContractsStagePayload,
    IntegrationStagePayload,
    RequirementsStagePayload,
)


def test_requirements_payload_rejects_missing_or_duplicate_requirements():
    with pytest.raises(ValidationError):
        RequirementsStagePayload.model_validate({"requirements": []})

    with pytest.raises(ValidationError, match="requirement_id values must be unique"):
        RequirementsStagePayload.model_validate({
            "requirements": [
                {"requirement_id": "REQ-1", "description": "First"},
                {"requirement_id": "REQ-1", "description": "Duplicate"},
            ]
        })


def test_stage_payloads_reject_unknown_fields_and_invalid_shapes():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RequirementsStagePayload.model_validate({
            "requirements": [{"requirement_id": "REQ-1", "description": "First"}],
            "unexpected": True,
        })

    with pytest.raises(ValidationError):
        RequirementsStagePayload.model_validate({"requirements": "not-a-list"})


def test_architecture_payload_checks_paths_and_dependency_graph():
    with pytest.raises(ValidationError, match="workspace-relative and safe"):
        ArchitectureStagePayload.model_validate({
            "architecture": [{
                "module_id": "MOD-1",
                "name": "Root",
                "responsibility": "Root files",
                "file_paths": ["../outside.txt"],
            }]
        })

    with pytest.raises(ValidationError, match="dependency graph contains a cycle"):
        ArchitectureStagePayload.model_validate({
            "architecture": [
                {"module_id": "MOD-1", "name": "One", "responsibility": "One", "depends_on": ["MOD-2"]},
                {"module_id": "MOD-2", "name": "Two", "responsibility": "Two", "depends_on": ["MOD-1"]},
            ]
        })


def test_contract_stage_allows_no_contracts_for_infrastructure_tasks():
    payload = ContractsStagePayload.model_validate({"contracts": []})
    assert payload.model_dump() == {"contracts": []}


def test_integration_payload_checks_unit_references_and_accepts_legacy_verification_ids():
    with pytest.raises(ValidationError, match="references unknown dependencies"):
        IntegrationStagePayload.model_validate({
            "implementation_units": [{
                "unit_id": "UNIT-1",
                "title": "Create file",
                "target_path": "file.txt",
                "depends_on": ["UNIT-MISSING"],
            }],
            "verification": [{"description": "Check file", "kind": "static"}],
        })

    payload = IntegrationStagePayload.model_validate({
        "implementation_units": [],
        "verification": [{"id": "VER-1", "description": "Check file", "kind": "static"}],
    })
    assert payload.verification[0].criterion_id == "VER-1"


def test_requirement_payload_assigns_stable_acceptance_criterion_ids():
    payload = RequirementsStagePayload.model_validate({
        "requirements": [{
            "requirement_id": "REQ-LOGIN",
            "description": "Users can sign in.",
            "acceptance_criteria": ["Valid credentials sign in.", "Invalid credentials are rejected."],
        }]
    })

    ids = payload.requirements[0].acceptance_criterion_ids
    assert len(ids) == 2
    assert ids[0].startswith("REQ-LOGIN-AC-")
    assert ids[1].startswith("REQ-LOGIN-AC-")
    reordered = RequirementsStagePayload.model_validate({
        "requirements": [{
            "requirement_id": "REQ-LOGIN",
            "description": "Users can sign in.",
            "acceptance_criteria": ["Invalid credentials are rejected.", "Valid credentials sign in."],
        }]
    })
    assert dict(zip(
        payload.requirements[0].acceptance_criteria, ids
    )) == dict(zip(
        reordered.requirements[0].acceptance_criteria,
        reordered.requirements[0].acceptance_criterion_ids,
    ))


def test_requirement_payload_rejects_misaligned_acceptance_criterion_ids():
    with pytest.raises(ValidationError, match="one ID for each acceptance criterion"):
        RequirementsStagePayload.model_validate({
            "requirements": [{
                "requirement_id": "REQ-LOGIN",
                "description": "Users can sign in.",
                "acceptance_criteria": ["Valid credentials sign in.", "Invalid credentials are rejected."],
                "acceptance_criterion_ids": ["REQ-LOGIN-AC-custom"],
            }]
        })


def test_requirements_payload_rejects_duplicate_acceptance_criterion_ids_across_requirements():
    with pytest.raises(ValidationError, match="unique across requirements"):
        RequirementsStagePayload.model_validate({
            "requirements": [
                {
                    "requirement_id": "REQ-1",
                    "description": "First requirement",
                    "acceptance_criteria": ["First criterion"],
                    "acceptance_criterion_ids": ["SHARED-AC-001"],
                },
                {
                    "requirement_id": "REQ-2",
                    "description": "Second requirement",
                    "acceptance_criteria": ["Second criterion"],
                    "acceptance_criterion_ids": ["SHARED-AC-001"],
                },
            ]
        })
