"""Validated evidence contracts for acceptance-driven verification."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VerificationEvidenceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"
    BLOCKED = "BLOCKED"


class EvidenceSource(str, Enum):
    COMMAND = "COMMAND"
    TEST = "TEST"
    STRUCTURAL = "STRUCTURAL"
    MANUAL = "MANUAL"


class VerificationEvidenceRecord(BaseModel):
    """A provenance-bearing observation; it is not itself an authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    evidence_id: str = ""
    criterion_id: str = Field(min_length=1)
    source: EvidenceSource
    status: VerificationEvidenceStatus
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    statement: str = ""
    reviewer: str = ""
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""

    @model_validator(mode="after")
    def validate_provenance_and_status(self):
        if self.source in {EvidenceSource.COMMAND, EvidenceSource.TEST, EvidenceSource.STRUCTURAL}:
            if not self.command or any(not part.strip() for part in self.command):
                raise ValueError("command evidence requires a non-empty exact command")
            if self.status == VerificationEvidenceStatus.PASS and (
                self.exit_code != 0 or self.timed_out
            ):
                raise ValueError("command evidence can PASS only with exit code 0 and no timeout")
            if self.status == VerificationEvidenceStatus.FAIL and not (
                self.timed_out or (self.exit_code is not None and self.exit_code != 0)
            ):
                raise ValueError("command evidence can FAIL only after a non-zero exit or timeout")
            if self.status == VerificationEvidenceStatus.BLOCKED and (
                self.exit_code is not None or self.timed_out
            ):
                raise ValueError("blocked command evidence must have no exit code and must not be timed out")
            if self.status == VerificationEvidenceStatus.NOT_VERIFIED:
                raise ValueError("missing command evidence must not be represented as an evidence record")
        else:
            if self.status == VerificationEvidenceStatus.NOT_VERIFIED:
                raise ValueError("missing manual evidence must not be represented as an evidence record")
            if not self.reviewer:
                raise ValueError("manual evidence requires a reviewer identity")
            if not self.statement:
                raise ValueError("manual evidence requires a non-empty statement")
        if not self.evidence_id:
            payload: dict[str, Any] = {
                "criterion_id": self.criterion_id,
                "source": self.source.value,
                "status": self.status.value,
                "observed_at": self.observed_at.isoformat(),
                "statement": self.statement,
                "reviewer": self.reviewer,
                "command": list(self.command),
                "exit_code": self.exit_code,
                "timed_out": self.timed_out,
                "stdout": self.stdout,
                "stderr": self.stderr,
            }
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:20]
            object.__setattr__(self, "evidence_id", f"EVD-{digest}")
        return self
