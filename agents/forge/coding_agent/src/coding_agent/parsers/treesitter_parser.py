"""Shared Tree-sitter adapter for language-specific source parsers."""

from __future__ import annotations

import re
from typing import ClassVar

from coding_agent.parsers.base import (
    ImportRecord, ParsedSource, ParseDiagnostic, ParseFailure, ReferenceRecord, Symbol,
)


class TreeSitterParser:
    """Extract common structural information from a Tree-sitter syntax tree.

    Subclasses provide grammar-specific node mappings and import extraction.
    Tree-sitter grammars are loaded lazily on first parse.
    """

    language: ClassVar[str]
    grammar_name: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]
    symbol_node_kinds: ClassVar[dict[str, str]] = {}
    scope_node_kinds: ClassVar[frozenset[str]] = frozenset()
    import_node_kinds: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        self._parser = None

    def _get_parser(self):
        if self._parser is None:
            try:
                from tree_sitter_language_pack import get_parser
            except ImportError as exc:
                raise RuntimeError(
                    "Tree-sitter support requires "
                    "'tree-sitter-language-pack'. Install project dependencies."
                ) from exc

            try:
                self._parser = get_parser(self.grammar_name)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not load the Tree-sitter grammar "
                    f"{self.grammar_name!r} for {self.language}."
                ) from exc

        return self._parser

    def parse(self, relative_path: str, source: str) -> ParsedSource:
        tree = self._get_parser().parse(source.encode("utf-8"))
        if tree is None:
            raise SyntaxError(
                f"Tree-sitter could not parse {relative_path!r} "
                f"as {self.language}."
            )

        root = tree.root_node
        source_bytes = source.encode("utf-8")
        if root.has_error:
            diagnostics = self._collect_syntax_diagnostics(root)
            raise ParseFailure(
                f"Syntax errors detected while parsing {relative_path!r} "
                f"as {self.language}.",
                diagnostics,
            )

        symbols: list[Symbol] = []
        imports: list[ImportRecord] = []
        self._visit(
            root,
            relative_path=relative_path,
            source_bytes=source_bytes,
            scopes=[],
            symbols=symbols,
            imports=imports,
        )

        symbols.extend(
            self._extract_additional_symbols(
                root,
                relative_path=relative_path,
                source_bytes=source_bytes,
            )
        )
        imports.sort(key=lambda item: item.line)
        references = self._extract_references(root, source_bytes)
        return ParsedSource(
            symbols=tuple(symbols),
            imports=tuple(imports),
            references=tuple(references),
        )


    @staticmethod
    def _collect_syntax_diagnostics(root) -> tuple[ParseDiagnostic, ...]:
        """Collect Tree-sitter ERROR and missing-token locations."""
        diagnostics: list[ParseDiagnostic] = []
        stack = [root]
        while stack:
            node = stack.pop()
            missing = bool(getattr(node, "is_missing", False))
            if getattr(node, "type", "") == "ERROR" or missing:
                start = getattr(node, "start_point", None)
                end = getattr(node, "end_point", start)
                if start is None:
                    stack.extend(reversed(getattr(node, "children", ())))
                    continue
                diagnostics.append(
                    ParseDiagnostic(
                        message=(f"Missing {node.type} token" if missing else "Unexpected or invalid syntax"),
                        line=start.row + 1,
                        column=start.column + 1,
                        end_line=end.row + 1,
                        end_column=end.column + 1,
                        code="treesitter.missing-token" if missing else "treesitter.syntax-error",
                    )
                )
            stack.extend(reversed(getattr(node, "children", ())))
        return tuple(sorted(diagnostics, key=lambda item: (item.line, item.column, item.code or "")))

    def _visit(
        self,
        node,
        *,
        relative_path: str,
        source_bytes: bytes,
        scopes: list[str],
        symbols: list[Symbol],
        imports: list[ImportRecord],
    ) -> None:
        node_type = node.type

        if node_type in self.import_node_kinds:
            imports.extend(self._parse_imports(node, source_bytes))

        symbol_kind = self._get_symbol_kind(node, source_bytes)
        symbol_name = (
            self._get_symbol_name(node, source_bytes)
            if symbol_kind
            else None
        )

        if symbol_kind and symbol_name:
            symbols.append(
                self._build_symbol(
                    node,
                    relative_path=relative_path,
                    source_bytes=source_bytes,
                    scopes=scopes,
                    symbol_name=symbol_name,
                    symbol_kind=symbol_kind,
                )
            )

        should_push_scope = self._is_scope_node(
            node, symbol_kind, symbol_name, source_bytes=source_bytes
        )
        if should_push_scope:
            scopes.append(symbol_name)

        for child in node.named_children:
            self._visit(
                child,
                relative_path=relative_path,
                source_bytes=source_bytes,
                scopes=scopes,
                symbols=symbols,
                imports=imports,
            )

        if should_push_scope:
            scopes.pop()


    def _parse_imports(self, node, source_bytes: bytes) -> tuple[ImportRecord, ...]:
        """Extract zero or more import records from one syntax node."""
        record = self._parse_import(node, source_bytes)
        return (record,) if record is not None else ()

    def _get_symbol_kind(self, node, source_bytes: bytes) -> str | None:
        """Return the semantic kind for a node, if it represents a symbol."""
        return self.symbol_node_kinds.get(node.type)

    def _is_scope_node(
        self,
        node,
        symbol_kind: str | None,
        symbol_name: str | None,
        source_bytes: bytes | None = None,
    ) -> bool:
        return node.type in self.scope_node_kinds and bool(symbol_name)

    def _build_symbol(
        self,
        node,
        *,
        relative_path: str,
        source_bytes: bytes,
        scopes: list[str],
        symbol_name: str,
        symbol_kind: str,
    ) -> Symbol:
        parameters = node.child_by_field_name("parameters")
        signature = (
            self._node_text(parameters, source_bytes)
            if parameters is not None
            else None
        )
        return_type_node = node.child_by_field_name("return_type")
        return_type = (
            self._node_text(return_type_node, source_bytes).lstrip(": ").strip()
            if return_type_node is not None
            else None
        )
        declared_type = None
        if symbol_kind in {
            "field", "variable", "variable_declaration", "constant", "static"
        }:
            for candidate in self._walk_nodes(node):
                type_node = candidate.child_by_field_name("type")
                if type_node is not None:
                    declared_type = self._node_text(type_node, source_bytes).strip()
                    if declared_type:
                        break
        qualified_name = ".".join([*scopes, symbol_name])

        # Include modifiers on the declaration line and in its syntactic
        # header. Restrict the scan to the header so words in method bodies,
        # comments, and string literals cannot become modifiers.
        line_start = source_bytes.rfind(b"\n", 0, node.start_byte) + 1
        prefix = source_bytes[line_start:node.start_byte]
        body_node_types = {
            "block", "class_body", "declaration_list", "enum_body",
            "interface_body", "struct_body", "namespace_body",
            "field_declaration_list", "compound_statement", "accessor_list",
            "arrow_expression_clause",
        }
        body_starts = [
            child.start_byte
            for child in node.named_children
            if child.type in body_node_types
        ]
        if body_starts:
            header_end = min(body_starts)
        else:
            declaration_bytes = source_bytes[node.start_byte:node.end_byte]
            cut_points = [
                position for token in (b";", b"=", b"=>")
                if (position := declaration_bytes.find(token)) >= 0
            ]
            header_end = (
                node.start_byte + min(cut_points)
                if cut_points else node.end_byte
            )
        header = source_bytes[node.start_byte:header_end]
        header_text = (prefix + header).decode("utf-8", errors="replace")
        header_text = re.sub(r"/\*.*?\*/|//[^\n]*", " ", header_text, flags=re.DOTALL)

        modifier_candidates = (
            "public", "private", "protected", "internal", "static",
            "abstract", "virtual", "override", "sealed", "readonly",
            "async", "extern", "unsafe", "partial", "final",
            "synchronized", "native", "default", "const", "volatile",
            "inline", "constexpr", "explicit", "friend", "pub",
            "mut", "export", "extern", "register", "auto", "inline",
        )
        modifiers = tuple(
            modifier
            for modifier in dict.fromkeys(modifier_candidates)
            if re.search(r"(?<!\w)" + re.escape(modifier) + r"(?!\w)", header_text)
        )
        return Symbol(
            name=symbol_name,
            qualified_name=qualified_name,
            kind=symbol_kind,
            relative_path=relative_path,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=signature,
            return_type=return_type,
            modifiers=modifiers,
            declared_type=declared_type,
        )

    @staticmethod
    def _walk(node):
        """Yield a syntax node and all of its named descendants.

        Keep this compatibility-friendly name in the shared adapter so
        language parsers do not need to inherit the configurable
        GrammarParser merely to traverse syntax trees.
        """
        yield node
        for child in node.named_children:
            yield from TreeSitterParser._walk(child)

    @staticmethod
    def _walk_nodes(node):
        """Explicitly named alias for recursive named-node traversal."""
        yield from TreeSitterParser._walk(node)

    def _find_descendant(self, node, node_types: set[str]):
        """Return the first matching named descendant, excluding *node*."""
        for candidate in self._walk(node):
            if candidate is not node and candidate.type in node_types:
                return candidate
        return None

    def _find_declarator_name(self, node, source_bytes: bytes) -> str | None:
        """Find the identifier belonging to a declarator, not its type."""
        declarator = node.child_by_field_name("declarator")
        if declarator is not None and declarator is not node:
            return self._find_declarator_name(declarator, source_bytes)

        if node.type in {
            "identifier", "field_identifier", "type_identifier",
            "namespace_identifier", "property_identifier",
        }:
            return self._node_text(node, source_bytes).strip()

        preferred = {
            "init_declarator", "function_declarator", "pointer_declarator",
            "array_declarator", "parenthesized_declarator",
            "reference_declarator", "qualified_identifier",
            "variable_declarator", "field_declarator",
        }
        children = list(node.named_children)
        for child in children:
            if child.type in preferred:
                found = self._find_declarator_name(child, source_bytes)
                if found:
                    return found
        for child in children:
            found = self._find_declarator_name(child, source_bytes)
            if found:
                return found
        return None

    def _extract_additional_symbols(
        self,
        root,
        *,
        relative_path: str,
        source_bytes: bytes,
    ) -> list[Symbol]:
        """Extract additional language facts not represented by one AST node."""
        return []

    def _extract_references(self, root, source_bytes: bytes) -> list[ReferenceRecord]:
        """Extract language-specific references without resolving them."""
        return []

    def _get_symbol_name(
        self,
        node,
        source_bytes: bytes,
    ) -> str | None:
        """Extract a symbol name using the grammar's name field."""

        name_node = node.child_by_field_name("name")

        if name_node is None:
            return None

        return self._node_text(name_node, source_bytes)

    @staticmethod
    def _node_text(node, source_bytes: bytes) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode(
            "utf-8",
            errors="replace",
        )

    def _parse_import(
        self,
        node,
        source_bytes: bytes,
    ) -> ImportRecord | None:
        """Convert a grammar-specific import node into a shared record."""
        raise NotImplementedError
