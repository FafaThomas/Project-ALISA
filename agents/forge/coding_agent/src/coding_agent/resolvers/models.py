"""Shared relationship records produced by workspace resolution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Relationship:
    """A directed, evidence-backed relationship between workspace entities.

    ``target_*`` fields remain empty when a reference cannot be resolved.
    ``target_symbol`` then preserves the spelling found in source code, while
    ``candidates`` records possible destinations for ambiguous references.
    """

    kind: str
    source_path: str
    source_module: str
    source_symbol: str | None
    target_path: str | None
    target_module: str | None
    target_symbol: str | None
    line: int
    status: str = "resolved"
    evidence: str | None = None
    candidates: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"resolved", "unresolved", "ambiguous", "external"}:
            raise ValueError(f"Unsupported relationship status: {self.status!r}")
        if not self.kind.strip():
            raise ValueError("Relationship kind cannot be empty")
        if not self.source_path.strip():
            raise ValueError("Relationship source_path cannot be empty")
        if self.line < 0:
            raise ValueError("Relationship line cannot be negative")

    @property
    def source_id(self) -> str:
        if self.source_symbol:
            return f"{self.source_path}::{self.source_symbol}"
        return f"{self.source_path}::module"

    @property
    def target_id(self) -> str | None:
        if self.target_path is None:
            return None
        if self.target_symbol:
            return f"{self.target_path}::{self.target_symbol}"
        return f"{self.target_path}::module"
