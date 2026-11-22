"""Deterministic, conservative validation of generated artifacts against ESpec contracts.

Only high-confidence source patterns are evaluated. Unsupported language or
contract shapes are reported as UNVERIFIED, never silently passed.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable

from coding_agent.code_generator import ImplementationArtifact
from coding_agent.engineering_spec import EngineeringSpecification, InterfaceContract


class ContractCheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ContractFinding:
    contract_id: str
    status: ContractCheckStatus
    message: str
    missing_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractValidationReport:
    unit_id: str
    artifact_path: str
    findings: tuple[ContractFinding, ...]

    @property
    def passed(self) -> bool:
        return all(item.status == ContractCheckStatus.PASSED for item in self.findings)

    @property
    def failed(self) -> bool:
        return any(item.status == ContractCheckStatus.FAILED for item in self.findings)

    @property
    def unverified(self) -> bool:
        return any(item.status == ContractCheckStatus.UNVERIFIED for item in self.findings)


class ContractComplianceValidator:
    """Check high-confidence function/component input contracts for one artifact."""

    def validate(
        self,
        specification: EngineeringSpecification,
        artifact: ImplementationArtifact,
    ) -> ContractValidationReport:
        units = {unit.unit_id: unit for unit in specification.implementation_units}
        unit = units.get(artifact.unit_id)
        if unit is None:
            raise ValueError(f"artifact references unknown implementation unit: {artifact.unit_id}")
        if artifact.path != unit.target_path:
            raise ValueError("artifact path does not match its authorized implementation target")
        contracts = {item.contract_id: item for item in specification.contracts}
        findings = tuple(
            self._check(contracts[contract_id], artifact)
            for contract_id in sorted(unit.implements)
        )
        return ContractValidationReport(unit.unit_id, artifact.path, findings)

    def _check(self, contract: InterfaceContract, artifact: ImplementationArtifact) -> ContractFinding:
        required = tuple(field.name for field in contract.inputs if field.required)
        suffix = artifact.path.rsplit(".", 1)[-1].lower() if "." in artifact.path else ""
        if suffix == "py":
            try:
                tree = ast.parse(artifact.content)
            except SyntaxError as exc:
                return ContractFinding(contract.contract_id, ContractCheckStatus.FAILED,
                                       f"Python syntax error: {exc.msg}")
            candidates = [node for node in ast.walk(tree)
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and node.name == contract.name]
            if not candidates:
                return ContractFinding(contract.contract_id, ContractCheckStatus.FAILED,
                                       f"declared contract symbol {contract.name!r} was not found")
            if isinstance(candidates[0], ast.ClassDef):
                available = {node.arg for child in candidates[0].body
                             if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                             for node in child.args.args + child.args.kwonlyargs}
                available.discard("self")
                available.discard("cls")
            else:
                args = candidates[0].args
                available = {node.arg for node in args.args + args.kwonlyargs}
                if args.vararg:
                    available.add(args.vararg.arg)
                if args.kwarg:
                    available.add(args.kwarg.arg)
            missing = tuple(name for name in required if name not in available)
            if missing:
                return ContractFinding(contract.contract_id, ContractCheckStatus.FAILED,
                                       "required contract inputs are absent from the symbol signature", missing)
            return ContractFinding(contract.contract_id, ContractCheckStatus.PASSED,
                                   "symbol exists and required inputs are present")

        if suffix in {"js", "jsx", "ts", "tsx"}:
            source = artifact.content
            # Deliberately recognize only named function declarations with a single
            # destructured parameter; other patterns remain unverified.
            pattern = re.compile(
                rf"(?:function\s+{re.escape(contract.name)}\s*\(\s*\{{([^}}]*)\}}\s*\)|"
                rf"(?:const|let|var)\s+{re.escape(contract.name)}\s*=\s*\(\s*\{{([^}}]*)\}}\s*\)\s*=>)",
                re.MULTILINE,
            )
            match = pattern.search(source)
            if not match:
                return ContractFinding(contract.contract_id, ContractCheckStatus.UNVERIFIED,
                                       "JavaScript/TypeScript symbol signature is outside supported patterns")
            params = match.group(1) if match.group(1) is not None else match.group(2)
            available = {part.strip().split(":", 1)[0].split("=", 1)[0].strip()
                         for part in params.split(",") if part.strip()}
            missing = tuple(name for name in required if name not in available)
            if missing:
                return ContractFinding(contract.contract_id, ContractCheckStatus.FAILED,
                                       "required contract inputs are absent from the component parameters", missing)
            return ContractFinding(contract.contract_id, ContractCheckStatus.PASSED,
                                   "component exists and required inputs are present")

        return ContractFinding(contract.contract_id, ContractCheckStatus.UNVERIFIED,
                               f"contract validation is not implemented for .{suffix or '<unknown>'} files")
