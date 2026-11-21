
"""Deterministic Python source index: symbols, imports, and dependencies."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

from coding_agent.workspace import Workspace, WorkspaceError


IGNORED_DIRECTORIES = {
    ".git", ".venv", "venv", "env",
    "__pycache__", "node_modules",
    "dist", "build", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox",
}

SOURCE_ROOT_MARKERS = {"src", "lib", "app"}


@dataclass(frozen=True)
class Symbol:
    """A class, function, or method defined in a Python module."""

    name: str
    qualified_name: str
    kind: str
    relative_path: str
    line: int
    end_line: int
    signature: str | None = None


@dataclass(frozen=True)
class ImportRecord:
    """An import statement found in a Python module."""

    module: str | None
    imported_names: tuple[str, ...]
    relative_level: int
    line: int


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
    """Structural information extracted from one Python file."""

    relative_path: str
    module_names: tuple[str, ...]
    symbols: tuple[Symbol, ...]
    imports: tuple[ImportRecord, ...]
    parse_error: str | None = None


@dataclass(frozen=True)
class CodeIndex:
    """Immutable index of the Python files in a workspace."""

    modules: tuple[ModuleRecord, ...]
    dependencies: tuple[Dependency, ...]
    discovered_files: int
    indexed_files: int
    failed_files: int

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

    def render(self) -> str:
        """Render a compact structural overview for an LLM."""

        sections = [
            "PYTHON CODE STRUCTURE",
            f"Discovered Python files: {self.discovered_files}",
            f"Indexed modules: {self.indexed_files}",
            f"Files with parse errors: {self.failed_files}",
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
                continue

            for symbol in module.symbols:
                sections.append(
                    f"  {symbol.kind}: {symbol.qualified_name} "
                    f"(lines {symbol.line}-{symbol.end_line})"
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

        return "\n".join(sections)


class _SymbolVisitor(ast.NodeVisitor):
    """Extract definitions while retaining their qualified scope."""

    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.symbols: list[Symbol] = []
        self.class_depth = 0

    def _visit_definition(self, node, kind: str) -> None:
        name = node.name
        qualified = ".".join([*self.scope, name])

        signature = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signature = ast.unparse(node.args)

        self.symbols.append(
            Symbol(
                name=name,
                qualified_name=qualified,
                kind=kind,
                relative_path=self.relative_path,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                signature=signature,
            )
        )

        self.scope.append(name)
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_depth += 1
        self._visit_definition(node, "class")
        self.class_depth -= 1


    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self.class_depth else "function"
        self._visit_definition(node, kind)


    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = "method" if self.class_depth else "function"
        self._visit_definition(node, kind)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Lambda expressions are not named declarations.
        return

    def extract(self, tree: ast.AST, source: str) -> tuple[Symbol, ...]:
        self.source = source
        self.visit(tree)
        return tuple(self.symbols)


class CodeIndexer:
    """Build a Python symbol and internal dependency index."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        max_files: int = 2000,
    ) -> None:
        if max_files < 1:
            raise ValueError("max_files must be positive")

        self.workspace = workspace
        self.max_files = max_files

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

    @staticmethod
    def _collect_imports(tree: ast.AST) -> tuple[ImportRecord, ...]:
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        ImportRecord(
                            module=alias.name,
                            imported_names=(alias.asname or alias.name,),
                            relative_level=0,
                            line=node.lineno,
                        )
                    )

            elif isinstance(node, ast.ImportFrom):
                imports.append(
                    ImportRecord(
                        module=node.module,
                        imported_names=tuple(
                            alias.name for alias in node.names
                        ),
                        relative_level=node.level,
                        line=node.lineno,
                    )
                )

        return tuple(sorted(imports, key=lambda item: item.line))

    @staticmethod
    def _resolve_relative_import(
        source_module: str,
        import_record: ImportRecord,
        is_package: bool,
    ) -> str | None:
        if import_record.relative_level == 0:
            return import_record.module

        parts = source_module.split(".")

        # A package's __init__.py imports are relative to the package itself.
        if not is_package:
            parts = parts[:-1]

        remove_count = import_record.relative_level - 1

        if remove_count > len(parts):
            return None

        if remove_count:
            parts = parts[:len(parts) - remove_count]

        if import_record.module:
            parts.extend(import_record.module.split("."))

        return ".".join(parts) if parts else None

    def _discover_python_files(self) -> list[str]:
        root = self.workspace.root
        discovered = []

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

                if filename.lower().endswith(".py") and not path.is_symlink():
                    discovered.append(
                        path.relative_to(root).as_posix()
                    )

                    if len(discovered) >= self.max_files:
                        return discovered

        return discovered

    def build(self) -> CodeIndex:
        """Discover, parse, and connect Python modules."""

        paths = self._discover_python_files()
        modules = []

        for relative_path in paths:
            aliases = self._module_aliases(relative_path)

            try:
                snapshot = self.workspace.read_text(relative_path)
                tree = ast.parse(
                    snapshot.content,
                    filename=relative_path,
                )

                visitor = _SymbolVisitor(relative_path)
                symbols = visitor.extract(tree, snapshot.content)
                imports = self._collect_imports(tree)

                modules.append(
                    ModuleRecord(
                        relative_path=relative_path,
                        module_names=aliases,
                        symbols=symbols,
                        imports=imports,
                    )
                )

            except (SyntaxError, UnicodeError, WorkspaceError, OSError) as exc:
                modules.append(
                    ModuleRecord(
                        relative_path=relative_path,
                        module_names=aliases,
                        symbols=(),
                        imports=(),
                        parse_error=str(exc),
                    )
                )

        module_lookup = {
            alias: module
            for module in modules
            for alias in module.module_names
        }

        dependencies = []

        for module in modules:
            if module.parse_error or not module.module_names:
                continue

            source_name = module.module_names[-1]
            is_package = Path(module.relative_path).name == "__init__.py"

            for imported in module.imports:
                target_name = self._resolve_relative_import(
                    source_name,
                    imported,
                    is_package,
                )

                if not target_name:
                    continue

                possible_targets = [
                    f"{target_name}.{name}"
                    for name in imported.imported_names
                    if name != "*"
                ]

                possible_targets.append(target_name)

                resolved = next(
                    (
                        candidate
                        for candidate in possible_targets
                        if candidate in module_lookup
                    ),
                    None,
                )

                if resolved is None:
                    continue

                dependencies.append(
                    Dependency(
                        source_module=source_name,
                        target_module=resolved,
                        relative_path=module.relative_path,
                        line=imported.line,
                        imported_names=imported.imported_names,
                    )
                )

        return CodeIndex(
            modules=tuple(modules),
            dependencies=tuple(dependencies),
            discovered_files=len(paths),
            indexed_files=sum(
                module.parse_error is None for module in modules
            ),
            failed_files=sum(
                module.parse_error is not None for module in modules
            ),
        )
