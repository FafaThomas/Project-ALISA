"""Shared contracts for language-specific source parsers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Symbol:
    """A named definition extracted from source code."""

    name: str
    qualified_name: str
    kind: str
    relative_path: str
    line: int
    end_line: int
    signature: str | None = None
    return_type: str | None = None
    modifiers: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    is_exported: bool = False
    is_async: bool = False
    role: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    declared_type: str | None = None


@dataclass(frozen=True)
class ImportRecord:
    """An import or reference statement extracted from source code."""

    module: str | None
    imported_names: tuple[str, ...]
    relative_level: int
    line: int
    kind: str = "import"
    is_type_only: bool = False
    alias: str | None = None
    is_wildcard: bool = False
    is_static: bool = False
    is_global: bool = False
    is_public: bool = False
    is_system: bool = False
    aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ReferenceRecord:
    """A source-level reference that may later be resolved by an indexer."""

    target: str
    kind: str
    line: int
    context: str | None = None


@dataclass(frozen=True)
class ParseDiagnostic:
    """A parser diagnostic with one-based source coordinates."""

    message: str
    line: int
    column: int
    end_line: int | None = None
    end_column: int | None = None
    severity: str = "error"
    code: str | None = None


class ParseFailure(SyntaxError):
    """Syntax failure carrying structured diagnostics when available."""

    def __init__(self, message: str, diagnostics: tuple[ParseDiagnostic, ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class ParsedSource:
    """Language-independent structural information from one source file."""

    symbols: tuple[Symbol, ...]
    imports: tuple[ImportRecord, ...]
    references: tuple[ReferenceRecord, ...] = ()
    diagnostics: tuple[ParseDiagnostic, ...] = ()


class SourceParser(Protocol):
    """Contract implemented by every language-specific parser."""

    language: str
    extensions: tuple[str, ...]

    def parse(
        self,
        relative_path: str,
        source: str,
    ) -> ParsedSource:
        """Parse source text and return its structural representation."""
        ...