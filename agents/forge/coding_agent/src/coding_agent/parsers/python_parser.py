"""Python source parser based on the standard-library AST module."""

from __future__ import annotations

import ast

from coding_agent.parsers.base import (
    ImportRecord,
    ParsedSource,
    Symbol,
)


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

    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:
        kind = "method" if self.class_depth else "function"
        self._visit_definition(node, kind)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Lambda expressions are not named declarations.
        return

    def extract(self, tree: ast.AST) -> tuple[Symbol, ...]:
        self.visit(tree)
        return tuple(self.symbols)


class PythonParser:
    """Extract symbols and imports from Python source files."""

    language = "python"
    extensions = (".py",)

    def parse(
        self,
        relative_path: str,
        source: str,
    ) -> ParsedSource:
        tree = ast.parse(
            source,
            filename=relative_path,
        )

        visitor = _SymbolVisitor(relative_path)
        symbols = visitor.extract(tree)
        imports = self._collect_imports(tree)

        return ParsedSource(
            symbols=symbols,
            imports=imports,
        )

    @staticmethod
    def _collect_imports(
        tree: ast.AST,
    ) -> tuple[ImportRecord, ...]:
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        ImportRecord(
                            module=alias.name,
                            imported_names=(
                                alias.asname or alias.name,
                            ),
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

        return tuple(
            sorted(imports, key=lambda item: item.line)
        )