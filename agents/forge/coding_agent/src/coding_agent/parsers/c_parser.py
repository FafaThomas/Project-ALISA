"""Language-specific C structural parser."""

from __future__ import annotations

import re

from coding_agent.parsers.base import ImportRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class CParser(GrammarParser):
    language = "c"
    grammar_name = "c"
    extensions = (".c", ".h")
    symbol_node_kinds = {
        "function_definition": "function",
        "declaration": "declaration",
        "struct_specifier": "struct",
        "union_specifier": "union",
        "enum_specifier": "enum",
        "enumerator": "enum_value",
        "type_definition": "typedef",
        "field_declaration": "field",
        "preproc_def": "macro",
        "preproc_function_def": "macro",
    }
    scope_node_kinds = frozenset({
        "struct_specifier", "union_specifier", "enum_specifier"
    })
    import_node_kinds = frozenset({"preproc_include"})

    _INCLUDE = re.compile(r'^\s*#\s*include\s*[<"](?P<target>[^>"]+)[>"]')

    def _parse_import(self, node, source_bytes: bytes) -> ImportRecord | None:
        raw = self._node_text(node, source_bytes)
        match = self._INCLUDE.match(raw)
        if not match:
            return None
        return ImportRecord(
            module=match.group("target"),
            imported_names=(),
            relative_level=0,
            line=node.start_point.row + 1,
            kind="c_include",
            is_system="<" in raw and (raw.find("<") < raw.find('"') if '"' in raw else True),
        )

    def _get_symbol_kind(self, node, source_bytes: bytes) -> str | None:
        if node.type == "declaration":
            if self._find_descendant(node, {"function_declarator"}) is not None:
                return "function_declaration"
            return "variable_declaration"
        return super()._get_symbol_kind(node, source_bytes)

    def _get_symbol_name(self, node, source_bytes: bytes) -> str | None:
        if node.type in {
            "function_definition", "declaration", "field_declaration",
            "type_definition", "preproc_def", "preproc_function_def",
        }:
            found = self._find_declarator_name(node, source_bytes)
            if found:
                return found
        return super()._get_symbol_name(node, source_bytes)

    def _build_symbol(self, node, **kwargs) -> Symbol:
        symbol = super()._build_symbol(node, **kwargs)
        metadata = list(symbol.metadata)
        if node.type in {"preproc_def", "preproc_function_def"}:
            metadata.append(("preprocessor", "define"))
        if node.type in {"function_definition", "declaration"}:
            function_declarator = self._find_descendant(node, {"function_declarator"})
            if function_declarator is not None:
                parameters = function_declarator.child_by_field_name("parameters")
                if parameters is not None:
                    symbol = Symbol(
                        **{
                            **symbol.__dict__,
                            "signature": self._node_text(parameters, kwargs["source_bytes"]),
                        }
                    )
        return Symbol(**{**symbol.__dict__, "metadata": tuple(metadata)})
