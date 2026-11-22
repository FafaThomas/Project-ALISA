"""Language-specific C++ structural parser."""

from __future__ import annotations

import re

from coding_agent.parsers.base import ImportRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class CppParser(GrammarParser):
    language = "cpp"
    grammar_name = "cpp"
    extensions = (".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx")
    symbol_node_kinds = {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "union_specifier": "union",
        "namespace_definition": "namespace",
        "enum_specifier": "enum",
        "enumerator": "enum_value",
        "alias_declaration": "alias",
        "type_definition": "typedef",
        "field_declaration": "field",
        "namespace_alias_definition": "namespace_alias",
    }
    scope_node_kinds = frozenset({
        "class_specifier", "struct_specifier", "namespace_definition",
        "union_specifier", "enum_specifier",
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
            kind="cpp_include",
            is_system="<" in raw and (raw.find("<") < raw.find('"') if '"' in raw else True),
        )

    def _get_symbol_kind(self, node, source_bytes: bytes) -> str | None:
        if node.type in {"declaration", "field_declaration"}:
            is_function = self._find_descendant(
                node, {"function_declarator"}
            ) is not None
            if is_function:
                name = self._get_symbol_name(node, source_bytes) or ""
                enclosing = self._enclosing_type_name(node, source_bytes)
                if enclosing and name == enclosing:
                    return "constructor"
                if enclosing and name == f"~{enclosing}":
                    return "destructor"
                return "method" if enclosing else "function_declaration"
            return "field" if node.type == "field_declaration" else "variable_declaration"
        if node.type == "function_definition":
            name = self._get_symbol_name(node, source_bytes) or ""
            enclosing = self._enclosing_type_name(node, source_bytes)
            if enclosing and name == enclosing:
                return "constructor"
            if enclosing and name == f"~{enclosing}":
                return "destructor"
            return "method" if enclosing else "function"
        return super()._get_symbol_kind(node, source_bytes)

    def _enclosing_type_name(self, node, source_bytes: bytes) -> str | None:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.type in {"class_specifier", "struct_specifier"}:
                name_node = parent.child_by_field_name("name")
                if name_node is not None:
                    return self._node_text(name_node, source_bytes)
            parent = getattr(parent, "parent", None)
        return None

    def _get_symbol_name(self, node, source_bytes: bytes) -> str | None:
        if node.type in {
            "function_definition", "declaration", "field_declaration",
            "type_definition",
        }:
            found = self._find_declarator_name(node, source_bytes)
            if found:
                return found
        return super()._get_symbol_name(node, source_bytes)

    def _current_access(self, node, source_bytes: bytes) -> str | None:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.type in {"field_declaration_list", "declaration_list"}:
                access = "private" if self._enclosing_type_kind(node) == "class_specifier" else "public"
                for sibling in parent.named_children:
                    if sibling.start_byte >= node.start_byte:
                        break
                    if sibling.type == "access_specifier":
                        access = self._node_text(sibling, source_bytes).strip().rstrip(":")
                return access
            parent = getattr(parent, "parent", None)
        return None

    def _enclosing_type_kind(self, node) -> str | None:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if parent.type in {"class_specifier", "struct_specifier"}:
                return parent.type
            parent = getattr(parent, "parent", None)
        return None

    def _build_symbol(self, node, **kwargs) -> Symbol:
        symbol = super()._build_symbol(node, **kwargs)
        source_bytes = kwargs["source_bytes"]
        metadata = list(symbol.metadata)
        access = self._current_access(node, source_bytes)
        if access:
            metadata.append(("access", access))
        parent = getattr(node, "parent", None)
        if parent is not None and parent.type == "template_declaration":
            metadata.append(("template", "true"))
        if node.type in {"class_specifier", "struct_specifier"}:
            header = self._node_text(node, source_bytes).split("{", 1)[0]
            inheritance = re.search(r":\s*([^\{]+)", header)
            if inheritance:
                metadata.append(("inherits", inheritance.group(1).strip()))
        return Symbol(**{**symbol.__dict__, "metadata": tuple(metadata)})
