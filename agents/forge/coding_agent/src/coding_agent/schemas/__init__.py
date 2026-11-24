"""Structured schemas used at coding-agent model boundaries."""

from coding_agent.schemas.espec import (
    ArchitectureStagePayload,
    ContractsStagePayload,
    IntegrationStagePayload,
    RequirementsStagePayload,
)

__all__ = [
    "ArchitectureStagePayload",
    "ContractsStagePayload",
    "IntegrationStagePayload",
    "RequirementsStagePayload",
]

from coding_agent.schemas.verification import (
    EvidenceSource,
    VerificationEvidenceRecord,
    VerificationEvidenceStatus,
)

__all__ += [
    "EvidenceSource",
    "VerificationEvidenceRecord",
    "VerificationEvidenceStatus",
]
