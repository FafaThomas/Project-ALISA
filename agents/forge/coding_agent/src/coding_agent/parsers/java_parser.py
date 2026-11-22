"""Java source parser backed by the Tree-sitter Java grammar."""

from __future__ import annotations

import re
from dataclasses import replace

from coding_agent.parsers.base import ImportRecord, ParsedSource, Symbol
from coding_agent.parsers.treesitter_parser import TreeSitterParser


class JavaParser(TreeSitterParser):
    language = "java"
    grammar_name = "java"
    extensions = (".java",)

    symbol_node_kinds = {
        "package_declaration": "package",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "record",
        "annotation_type_declaration": "annotation",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
        "field_declaration": "field",
        "enum_constant": "enum_value",
        "annotation_type_element_declaration": "annotation_element",
        "compact_constructor_declaration": "constructor",
    }
    scope_node_kinds = frozenset({
        "package_declaration", "class_declaration", "interface_declaration",
        "enum_declaration", "record_declaration", "annotation_type_declaration",
    })
    import_node_kinds = frozenset({"import_declaration"})

    _IMPORT_PATTERN = re.compile(
        r"^\s*import\s+(?P<static>static\s+)?"
        r"(?P<target>[\w.*$]+)\s*;",
        re.DOTALL,
    )

    def parse(self, relative_path: str, source: str) -> ParsedSource:
        parsed = super().parse(relative_path, source)
        package_name = next(
            (symbol.name for symbol in parsed.symbols if symbol.kind == "package"),
            None,
        )
        if not package_name:
            return parsed
        qualified_symbols = tuple(
            symbol if symbol.kind == "package" else replace(
                symbol, qualified_name=f"{package_name}.{symbol.qualified_name}"
            )
            for symbol in parsed.symbols
        )
        return ParsedSource(
            symbols=qualified_symbols,
            imports=parsed.imports,
            references=parsed.references,
        )

    def _get_symbol_name(self, node, source_bytes: bytes) -> str | None:
        if node.type == "package_declaration" and node.named_children:
            return self._node_text(node.named_children[0], source_bytes)
        if node.type == "field_declaration":
            declarator = self._find_descendant(node, {"variable_declarator"})
            if declarator is not None:
                name_node = declarator.child_by_field_name("name")
                if name_node is not None:
                    return self._node_text(name_node, source_bytes)
                found = self._find_declarator_name(declarator, source_bytes)
                if found:
                    return found
        return super()._get_symbol_name(node, source_bytes)

    def _parse_import(self, node, source_bytes: bytes) -> ImportRecord | None:
        text = self._node_text(node, source_bytes)
        match = self._IMPORT_PATTERN.match(text)
        if not match:
            return None
        target = match.group("target")
        return ImportRecord(
            module=target,
            imported_names=(),
            relative_level=0,
            line=node.start_point.row + 1,
            kind="java_static_import" if match.group("static") else "java_import",
            is_static=bool(match.group("static")),
            is_wildcard=target.endswith(".*"),
        )

    def _build_symbol(self, node, **kwargs) -> Symbol:
        symbol = super()._build_symbol(node, **kwargs)
        source_bytes = kwargs["source_bytes"]
        annotations = tuple(
            self._node_text(candidate, source_bytes)
            for candidate in self._walk(node)
            if candidate.type in {"annotation", "marker_annotation"}
        )
        metadata = list(symbol.metadata)
        if node.type in {
            "class_declaration", "interface_declaration", "enum_declaration",
            "record_declaration",
        }:
            text = self._node_text(node, source_bytes)
            header = text.split("{", 1)[0]
            extends = re.search(r"\bextends\s+([^\s{]+)", header)
            implements = re.search(r"\bimplements\s+([^\{]+)", header)
            if extends:
                metadata.append(("extends", extends.group(1).strip()))
            if implements:
                metadata.append(("implements", implements.group(1).strip()))
        return Symbol(
            **{
                **symbol.__dict__,
                "is_exported": "public" in symbol.modifiers,
                "decorators": annotations,
                "metadata": tuple(metadata),
            }
        )

