
"""Deterministic polyglot source index: symbols, imports, and dependencies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from coding_agent.parsers import ParserRegistry, build_default_registry
from coding_agent.parsers.base import ImportRecord, ParseDiagnostic, ParseFailure, ReferenceRecord, Symbol
from coding_agent.workspace import Workspace, WorkspaceError
from coding_agent.resolvers import Relationship, RelationshipEngine


IGNORED_DIRECTORIES = {
    ".git", ".venv", "venv", "env",
    "__pycache__", "node_modules",
    "dist", "build", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox",
}

SOURCE_ROOT_MARKERS = {"src", "lib", "app"}


@dataclass(frozen=True)
class Dependency:
    """An internal module dependency."""

    source_module: str
    target_module: str
    relative_path: str
    line: int
    imported_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleRecord:
    """Structural information extracted from one source file."""

    relative_path: str
    module_names: tuple[str, ...]
    symbols: tuple[Symbol, ...]
    imports: tuple[ImportRecord, ...]
    language: str
    references: tuple[ReferenceRecord, ...] = ()
    parse_error: str | None = None
    diagnostics: tuple[ParseDiagnostic, ...] = ()


@dataclass(frozen=True)
class CodeIndex:
    """Immutable index of supported source files in a workspace."""

    modules: tuple[ModuleRecord, ...]
    dependencies: tuple[Dependency, ...]
    discovered_files: int
    indexed_files: int
    failed_files: int
    relationships: tuple[Relationship, ...] = ()

    @property
    def symbols(self) -> tuple[Symbol, ...]:
        return tuple(
            symbol
            for module in self.modules
            for symbol in module.symbols
        )

    def find_symbol(self, name: str) -> tuple[Symbol, ...]:
        """Find symbols by simple or qualified name."""

        return tuple(
            symbol
            for symbol in self.symbols
            if symbol.name == name or symbol.qualified_name == name
        )

    def find_module(self, name: str) -> tuple[ModuleRecord, ...]:
        """Find modules by any recognized module name."""

        return tuple(
            module
            for module in self.modules
            if name in module.module_names
        )

    def dependencies_of(self, module_name: str) -> tuple[Dependency, ...]:
        """Return outgoing internal dependencies for a module."""

        aliases = {
            alias
            for module in self.modules
            if module_name in module.module_names
            for alias in module.module_names
        }

        return tuple(
            dependency
            for dependency in self.dependencies
            if dependency.source_module in aliases
        )

    def dependents_of(self, module_name: str) -> tuple[Dependency, ...]:
        """Return modules that depend on the requested module."""

        aliases = {
            alias
            for module in self.modules
            if module_name in module.module_names
            for alias in module.module_names
        }

        return tuple(
            dependency
            for dependency in self.dependencies
            if dependency.target_module in aliases
        )

    def build_knowledge_graph(self):
        """Build a queryable graph from this index without reparsing files."""
        from coding_agent.knowledge_graph import KnowledgeGraph

        return KnowledgeGraph.from_code_index(self)

    @property
    def unresolved_relationships(self) -> tuple[Relationship, ...]:
        """Relationships that need more evidence or a language resolver."""
        return tuple(
            relationship
            for relationship in self.relationships
            if relationship.status in {"unresolved", "ambiguous"}
        )

    def relationships_from(self, relative_path: str) -> tuple[Relationship, ...]:
        """Return relationships whose source is the requested file."""
        normalized = relative_path.replace("\\", "/")
        return tuple(
            relationship
            for relationship in self.relationships
            if relationship.source_path == normalized
        )

    def render(self) -> str:
        """Render a compact structural overview for an LLM."""

        languages = sorted({module.language for module in self.modules})
        if len(languages) == 1 and languages[0] == "python":
            heading = "PYTHON CODE STRUCTURE"
            discovered_label = "Discovered Python files"
        else:
            heading = "CODEBASE STRUCTURE"
            discovered_label = "Discovered supported files"

        sections = [
            heading,
            f"{discovered_label}: {self.discovered_files}",
            f"Indexed modules: {self.indexed_files}",
            f"Files with parse errors: {self.failed_files}",
            f"Languages: {', '.join(languages) if languages else '(none)'}",
            "",
            "MODULES AND SYMBOLS:",
        ]

        for module in self.modules:
            primary_name = (
                module.module_names[-1]
                if module.module_names
                else module.relative_path
            )
            sections.append(f"\n[{primary_name}] {module.relative_path}")

            if module.parse_error:
                sections.append(f"  PARSE ERROR: {module.parse_error}")
                for diagnostic in module.diagnostics:
                    sections.append(
                        f"  {diagnostic.severity.upper()} {diagnostic.code or 'parse'} "
                        f"at {diagnostic.line}:{diagnostic.column}: {diagnostic.message}"
                    )
                continue

            for symbol in module.symbols:
                details = []
                if symbol.signature:
                    details.append(f"signature={symbol.signature}")
                if symbol.return_type:
                    details.append(f"returns={symbol.return_type}")
                if symbol.modifiers:
                    details.append(f"modifiers={', '.join(symbol.modifiers)}")
                if symbol.decorators:
                    details.append(f"decorators={', '.join(symbol.decorators)}")
                if symbol.is_exported:
                    details.append("exported")
                if symbol.is_async:
                    details.append("async")
                if symbol.role:
                    details.append(f"role={symbol.role}")
                suffix = f" [{'; '.join(details)}]" if details else ""
                sections.append(
                    f"  {symbol.kind}: {symbol.qualified_name} "
                    f"(lines {symbol.line}-{symbol.end_line}){suffix}"
                )
            for reference in module.references:
                sections.append(
                    f"  reference ({reference.kind}): {reference.target} "
                    f"(line {reference.line})"
                )

        sections.append("\nINTERNAL DEPENDENCIES:")

        if not self.dependencies:
            sections.append("  (No internal dependencies discovered.)")

        for dependency in self.dependencies:
            imported = (
                ", ".join(dependency.imported_names)
                if dependency.imported_names
                else "*"
            )
            sections.append(
                f"  {dependency.source_module} -> "
                f"{dependency.target_module} "
                f"(line {dependency.line}; imports: {imported})"
            )

        if self.relationships:
            sections.append("\nTYPED RELATIONSHIPS:")
            for relationship in self.relationships:
                source = relationship.source_symbol or relationship.source_module
                target = relationship.target_symbol or relationship.target_module or "(unresolved)"
                suffix = f" [{relationship.status}]"
                if relationship.candidates:
                    suffix += f" candidates={len(relationship.candidates)}"
                sections.append(
                    f"  {relationship.kind}: {source} -> {target} "
                    f"({relationship.source_path}:{relationship.line}){suffix}"
                )

        return "\n".join(sections)


class CodeIndexer:
    """Build a multi-language symbol and internal dependency index."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        max_files: int = 2000,
        parser_registry: ParserRegistry | None = None,
        relationship_engine: RelationshipEngine | None = None,
        parser_language_overrides: dict[str, str] | None = None,
    ) -> None:
        if max_files < 1:
            raise ValueError("max_files must be positive")

        self.workspace = workspace
        self.max_files = max_files
        self.parser_language_overrides = {
            key.lower() if key.startswith(".") else key.lower(): value.lower()
            for key, value in (parser_language_overrides or {}).items()
        }

        self.parser_registry = (
            parser_registry
            if parser_registry is not None
            else build_default_registry()
        )
        self.relationship_engine = (
            relationship_engine
            if relationship_engine is not None
            else RelationshipEngine()
        )

    @staticmethod
    def _module_aliases(relative_path: str) -> tuple[str, ...]:
        path = Path(relative_path)

        if path.name == "__init__.py":
            parts = list(path.parent.parts)
        else:
            parts = list(path.with_suffix("").parts)

        aliases = []

        if parts:
            aliases.append(".".join(parts))

        for index, part in enumerate(parts):
            if part.lower() in SOURCE_ROOT_MARKERS:
                suffix = parts[index + 1:]
                if suffix:
                    aliases.append(".".join(suffix))

        # Preserve order while removing duplicates.
        return tuple(dict.fromkeys(aliases))

    def _discover_source_files(self) -> list[str]:
        """Discover files supported by the registered parsers."""

        root = self.workspace.root
        discovered = set()

        supported_extensions = set(
            self.parser_registry.supported_extensions
        )
        supported_filenames = set(
            self.parser_registry.supported_filenames
        )

        for current, dirnames, filenames in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)

            dirnames[:] = [
                dirname
                for dirname in sorted(dirnames)
                if dirname.lower() not in IGNORED_DIRECTORIES
                and not (current_path / dirname).is_symlink()
            ]

            for filename in sorted(filenames):
                path = current_path / filename

                if path.is_symlink():
                    continue

                if (
                    path.suffix.lower() not in supported_extensions
                    and filename.lower() not in supported_filenames
                ):
                    continue

                discovered.add(
                    path.relative_to(root).as_posix()
                )

                if len(discovered) >= self.max_files:
                    return sorted(discovered)

        return sorted(discovered)

    def build(self) -> CodeIndex:
        """Discover, parse, and connect source modules."""

        paths = self._discover_source_files()
        modules = []

        for relative_path in paths:
            extension = Path(relative_path).suffix.lower()
            language_override = self.parser_language_overrides.get(extension)
            parser = self.parser_registry.get_parser(
                relative_path, language=language_override
            )

            if parser is None:
                continue

            aliases = self._module_aliases(relative_path)

            try:
                snapshot = self.workspace.read_text(relative_path)

                parsed = parser.parse(
                    relative_path,
                    snapshot.content,
                )

                modules.append(
                    ModuleRecord(
                        relative_path=relative_path,
                        module_names=aliases,
                        symbols=parsed.symbols,
                        imports=parsed.imports,
                        language=parser.language,
                        references=parsed.references,
                    )
                )

            except (
                SyntaxError,
                UnicodeError,
                WorkspaceError,
                OSError,
                RuntimeError,
            ) as exc:
                diagnostics = getattr(exc, "diagnostics", ())
                if isinstance(exc, SyntaxError) and not diagnostics:
                    line = getattr(exc, "lineno", None) or 1
                    column = getattr(exc, "offset", None) or 1
                    diagnostics = (
                        ParseDiagnostic(
                            message=getattr(exc, "msg", None) or str(exc),
                            line=line,
                            column=column,
                            end_line=getattr(exc, "end_lineno", None),
                            end_column=getattr(exc, "end_offset", None),
                            code="python.syntax-error" if parser.language == "python" else "parser.syntax-error",
                        ),
                    )
                modules.append(
                    ModuleRecord(
                        relative_path=relative_path,
                        module_names=aliases,
                        symbols=(),
                        imports=(),
                        parse_error=str(exc),
                        language=parser.language,
                        references=(),
                        diagnostics=tuple(diagnostics),
                    )
                )

        contextual_resolver = getattr(
            self.relationship_engine, "resolve_with_context", None
        )
        if contextual_resolver is not None:
            relationships = contextual_resolver(
                tuple(modules), workspace_root=self.workspace.root
            )
        else:
            # Preserve compatibility with custom relationship engines that
            # implement the original resolve(modules) interface.
            relationships = self.relationship_engine.resolve(tuple(modules))
        dependencies = tuple(
            Dependency(
                source_module=relationship.source_module,
                target_module=relationship.target_module or "",
                relative_path=relationship.source_path,
                line=relationship.line,
                imported_names=tuple(
                    item for item in dict(relationship.metadata).get("imported_names", "").split(",")
                    if item
                ),
            )
            for relationship in relationships
            if relationship.kind == "imports" and relationship.status == "resolved"
        )

        return CodeIndex(
            modules=tuple(modules),
            dependencies=dependencies,
            discovered_files=len(paths),
            indexed_files=sum(
                module.parse_error is None
                for module in modules
            ),
            failed_files=sum(
                module.parse_error is not None
                for module in modules
            ),
            relationships=relationships,
        )
