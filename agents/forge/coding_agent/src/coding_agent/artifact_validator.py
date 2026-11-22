"""Deterministic validation of generated implementation artifacts.

The validator is deliberately conservative: unsupported syntax and import
patterns are UNVERIFIED rather than silently accepted. It never writes files
or executes generated code.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
import re
from typing import Mapping, Sequence

from coding_agent.code_generator import ImplementationArtifact
from coding_agent.contract_validator import (
    ContractComplianceValidator,
    ContractCheckStatus,
)
from coding_agent.engineering_spec import EngineeringSpecification


class ArtifactCheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ArtifactFinding:
    check: str
    status: ArtifactCheckStatus
    message: str
    unit_id: str = ""
    path: str = ""


@dataclass(frozen=True)
class ArtifactValidationReport:
    findings: tuple[ArtifactFinding, ...]

    @property
    def passed(self) -> bool:
        return all(f.status == ArtifactCheckStatus.PASSED for f in self.findings)

    @property
    def failed(self) -> bool:
        return any(f.status == ArtifactCheckStatus.FAILED for f in self.findings)

    @property
    def unverified(self) -> bool:
        return any(f.status == ArtifactCheckStatus.UNVERIFIED for f in self.findings)

    @property
    def accepted(self) -> bool:
        """True only when no check failed or remains unverified."""
        return not self.failed and not self.unverified


class GeneratedArtifactValidator:
    """Validate artifact schema, paths, language, syntax, imports and contracts."""

    _LANGUAGES = {
        ".py": "python", ".pyi": "python",
        ".js": "javascript", ".jsx": "javascript",
        ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript",
        ".json": "json", ".toml": "toml", ".yaml": "yaml",
        ".yml": "yaml", ".md": "markdown", ".css": "css",
        ".scss": "scss", ".html": "html", ".sql": "sql",
    }

    def __init__(self, *, contract_validator: ContractComplianceValidator | None = None) -> None:
        self.contract_validator = contract_validator or ContractComplianceValidator()

    def validate(
        self,
        specification: EngineeringSpecification,
        artifacts: Sequence[ImplementationArtifact],
        *,
        project_files: Mapping[str, str] | None = None,
    ) -> ArtifactValidationReport:
        findings: list[ArtifactFinding] = []
        if not artifacts:
            findings.append(ArtifactFinding(
                "artifact_batch", ArtifactCheckStatus.UNVERIFIED,
                "no generated artifacts were supplied for validation",
            ))
            return ArtifactValidationReport(tuple(findings))
        units = {u.unit_id: u for u in specification.implementation_units}
        seen_units: set[str] = set()
        seen_paths: dict[str, str] = {}
        known_paths = set((project_files or {}).keys())

        for artifact in artifacts:
            if not isinstance(artifact, ImplementationArtifact):
                findings.append(ArtifactFinding(
                    "artifact_schema", ArtifactCheckStatus.FAILED,
                    "artifact must be an ImplementationArtifact",
                ))
                continue
            unit = units.get(artifact.unit_id)
            if unit is None:
                findings.append(self._finding("artifact_schema", "failed",
                    f"unknown implementation unit {artifact.unit_id!r}", artifact))
                continue
            if artifact.unit_id in seen_units:
                findings.append(self._finding("duplicate_unit", "failed",
                    "more than one artifact was supplied for the same implementation unit", artifact))
            seen_units.add(artifact.unit_id)

            path = artifact.path
            if not self._safe_relative_path(path):
                findings.append(self._finding("path_safety", "failed",
                    "artifact path must be a safe workspace-relative path", artifact))
                continue
            if path != unit.target_path:
                findings.append(self._finding("path_authorization", "failed",
                    f"artifact path does not match authorized target {unit.target_path!r}", artifact))
                continue
            normalized = path.replace(chr(92), "/")
            previous = seen_paths.get(normalized)
            if previous is not None:
                findings.append(self._finding("path_conflict", "failed",
                    f"multiple artifacts target the same path (first unit: {previous})", artifact))
            else:
                seen_paths[normalized] = artifact.unit_id
            if not isinstance(artifact.content, str) or not artifact.content.strip():
                findings.append(self._finding("artifact_schema", "failed",
                    "artifact content must be non-empty text", artifact))
                continue
            findings.append(self._finding("artifact_schema", "passed",
                "artifact structure and content are valid", artifact))

            suffix = PurePosixPath(normalized).suffix.lower()
            language = self._LANGUAGES.get(suffix)
            if language is None:
                findings.append(self._finding("language_detection", "unverified",
                    f"no validator is registered for file extension {suffix or '<none>'}", artifact))
                continue
            findings.append(self._finding("language_detection", "passed",
                f"detected {language}", artifact))

            if language == "python":
                try:
                    ast.parse(artifact.content, filename=path)
                except SyntaxError as exc:
                    findings.append(self._finding("syntax", "failed",
                        f"Python syntax error at line {exc.lineno}: {exc.msg}", artifact))
                else:
                    findings.append(self._finding("syntax", "passed",
                        "Python syntax is valid", artifact))
                findings.extend(self._python_import_findings(artifact, known_paths))
            elif language == "json":
                import json
                try:
                    json.loads(artifact.content)
                except (ValueError, TypeError) as exc:
                    findings.append(self._finding("syntax", "failed",
                        f"JSON parsing failed: {exc}", artifact))
                else:
                    findings.append(self._finding("syntax", "passed",
                        "JSON syntax is valid", artifact))
            else:
                findings.append(self._finding("syntax", "unverified",
                    f"syntax validation for {language} is not implemented", artifact))
                if language in {"javascript", "typescript"}:
                    findings.extend(self._js_import_findings(artifact, known_paths))

            try:
                contract_report = self.contract_validator.validate(specification, artifact)
            except (ValueError, TypeError) as exc:
                findings.append(self._finding("contract_compliance", "failed", str(exc), artifact))
            else:
                if not contract_report.findings:
                    findings.append(self._finding("contract_compliance", "passed",
                        "no interface contracts are assigned to this unit", artifact))
                for item in contract_report.findings:
                    status = {
                        ContractCheckStatus.PASSED: ArtifactCheckStatus.PASSED,
                        ContractCheckStatus.FAILED: ArtifactCheckStatus.FAILED,
                        ContractCheckStatus.UNVERIFIED: ArtifactCheckStatus.UNVERIFIED,
                    }[item.status]
                    findings.append(self._finding("contract_compliance", status.value,
                        f"{item.contract_id}: {item.message}", artifact))

        return ArtifactValidationReport(tuple(findings))

    @staticmethod
    def _safe_relative_path(path: object) -> bool:
        if not isinstance(path, str) or not path.strip() or "\x00" in path:
            return False
        normalized = path.replace(chr(92), "/")
        candidate = PurePosixPath(normalized)
        return (not candidate.is_absolute() and not candidate.anchor
                and not re.match(r"^[A-Za-z]:", normalized)
                and all(part not in {"", ".", ".."} for part in candidate.parts))

    @staticmethod
    def _finding(check: str, status: str, message: str,
                 artifact: ImplementationArtifact) -> ArtifactFinding:
        return ArtifactFinding(check, ArtifactCheckStatus(status), message,
                               artifact.unit_id, artifact.path)

    def _python_import_findings(self, artifact: ImplementationArtifact,
                                known_paths: set[str]) -> list[ArtifactFinding]:
        try:
            tree = ast.parse(artifact.content)
        except SyntaxError:
            return []
        findings = []
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("."):
                        module = alias.name.lstrip(".")
            if module and "." not in module and module not in {"os", "sys", "json", "re", "ast", "typing", "pathlib", "dataclasses", "enum", "collections"}:
                # Only flag an import when the project inventory makes its absence meaningful.
                candidate = module.replace(".", "/")
                if known_paths and not any(p.replace(chr(92), "/").endswith(candidate + ".py") or
                                           p.replace(chr(92), "/").endswith(candidate + "/__init__.py")
                                           for p in known_paths):
                    findings.append(self._finding("import_consistency", "unverified",
                        f"cannot resolve Python import {module!r} from supplied project inventory", artifact))
        if not findings:
            findings.append(self._finding("import_consistency", "unverified",
                "Python import resolution is not fully implemented", artifact))
        return findings

    def _js_import_findings(self, artifact: ImplementationArtifact,
                            known_paths: set[str]) -> list[ArtifactFinding]:
        findings = []
        local_imports = re.findall(
            r'''(?:from\s*|import\s*\()([\"'])(\.\.?/[^\"']+)\1''',
            artifact.content,
        )
        inventory = {p.replace(chr(92), "/") for p in known_paths}
        for _, target in local_imports:
            base = str(PurePosixPath(artifact.path).parent / target)
            candidates = [base, *(base + ext for ext in (".js", ".jsx", ".ts", ".tsx", ".json")),
                          base.rstrip("/") + "/index.js", base.rstrip("/") + "/index.jsx",
                          base.rstrip("/") + "/index.ts", base.rstrip("/") + "/index.tsx"]
            if inventory and not any(candidate in inventory for candidate in candidates):
                findings.append(self._finding("import_consistency", "failed",
                    f"local import {target!r} does not resolve in the supplied artifact/project inventory", artifact))
        if not findings:
            findings.append(self._finding("import_consistency", "unverified",
                "local import resolution is incomplete or no project inventory was supplied", artifact))
        return findings
