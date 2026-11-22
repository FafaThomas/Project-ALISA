"""C# source parser backed by the Tree-sitter C# grammar."""

from __future__ import annotations

import re
from dataclasses import replace

from coding_agent.parsers.base import ImportRecord, Symbol
from coding_agent.parsers.treesitter_parser import TreeSitterParser


class CSharpParser(TreeSitterParser):
    language = "csharp"
    grammar_name = "csharp"
    extensions = (".cs",)

    symbol_node_kinds = {
        "namespace_declaration": "namespace",
        "file_scoped_namespace_declaration": "namespace",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "struct_declaration": "struct",
        "record_declaration": "record",
        "enum_declaration": "enum",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
        "property_declaration": "property",
        "field_declaration": "field",
        "event_declaration": "event",
        "indexer_declaration": "indexer",
        "delegate_declaration": "delegate",
        "enum_member_declaration": "enum_value",
        "operator_declaration": "operator",
        "conversion_operator_declaration": "conversion_operator",
        "destructor_declaration": "destructor",
        "local_function_statement": "local_function",
    }
    scope_node_kinds = frozenset({
        "namespace_declaration", "file_scoped_namespace_declaration",
        "class_declaration", "interface_declaration", "struct_declaration",
        "record_declaration", "enum_declaration",
    })
    import_node_kinds = frozenset({"using_directive", "global_using_directive"})

    _USING_PATTERN = re.compile(
        r"^\s*(?P<global>global\s+)?using\s+"
        r"(?P<static>static\s+)?"
        r"(?:(?P<alias>[\w@]+)\s*=\s*)?"
        r"(?P<target>[^;]+)\s*;",
        re.DOTALL,
    )


    def parse(self, relative_path: str, source: str):
        parsed = super().parse(relative_path, source)
        namespace_match = re.search(
            r"(?m)^\s*namespace\s+([\w.]+)\s*;",
            source,
        )
        if not namespace_match:
            return parsed
        file_namespace = namespace_match.group(1)
        return type(parsed)(
            symbols=tuple(
                symbol if symbol.kind == "namespace" else replace(
                    symbol, qualified_name=f"{file_namespace}.{symbol.qualified_name}"
                )
                for symbol in parsed.symbols
            ),
            imports=parsed.imports,
            references=parsed.references,
        )

    def _get_symbol_name(self, node, source_bytes: bytes) -> str | None:
        if node.type in {"field_declaration", "event_declaration"}:
            declarator = self._find_descendant(node, {"variable_declarator"})
            if declarator is not None:
                name_node = declarator.child_by_field_name("name")
                if name_node is not None:
                    return self._node_text(name_node, source_bytes)
                found = self._find_declarator_name(declarator, source_bytes)
                if found:
                    return found
        return super()._get_symbol_name(node, source_bytes)

    def _build_symbol(self, node, **kwargs) -> Symbol:
        symbol = super()._build_symbol(node, **kwargs)
        source_bytes = kwargs["source_bytes"]
        attributes = tuple(
            self._node_text(candidate, source_bytes)
            for candidate in self._walk(node)
            if candidate.type == "attribute_list"
        )
        metadata = list(symbol.metadata)
        if node.type in {
            "class_declaration", "interface_declaration", "struct_declaration",
            "record_declaration",
        }:
            text = self._node_text(node, source_bytes)
            header = text.split("{", 1)[0]
            base_match = re.search(r"\s:\s*([^\{]+)", header)
            if base_match:
                base_types = tuple(
                    item.strip() for item in base_match.group(1).split(",")
                    if item.strip()
                )
                metadata.append(("base_types", ", ".join(base_types)))
        return Symbol(
            **{
                **symbol.__dict__,
                "is_exported": "public" in symbol.modifiers,
                "decorators": attributes,
                "metadata": tuple(metadata),
            }
        )

    def _parse_import(self, node, source_bytes: bytes) -> ImportRecord | None:
        text = self._node_text(node, source_bytes)
        match = self._USING_PATTERN.match(text)
        if not match:
            return None
        target = match.group("target").strip()
        alias = match.group("alias")
        return ImportRecord(
            module=target,
            imported_names=(alias,) if alias else (),
            relative_level=0,
            line=node.start_point.row + 1,
            kind="csharp_using",
            alias=alias,
            is_static=bool(match.group("static")),
            is_global=bool(match.group("global")),
        )
