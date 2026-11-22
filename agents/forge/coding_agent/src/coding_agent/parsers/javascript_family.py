"""Shared semantic extraction for JavaScript, TypeScript, JSX and TSX."""

from __future__ import annotations

import re
from dataclasses import replace

from coding_agent.parsers.base import ImportRecord, ParsedSource, ReferenceRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class JavaScriptFamilyParser(GrammarParser):
    """Extract declarations, module edges and JSX component references."""

    function_value_types = frozenset({
        "arrow_function", "function_expression", "generator_function",
    })
    function_scope_types = frozenset({
        "function_declaration", "generator_function_declaration",
        "method_definition", "arrow_function", "function_expression",
    })
    class_scope_types = frozenset({
        "class_declaration", "abstract_class_declaration",
        "interface_declaration", "enum_declaration",
    })
    property_node_kinds = frozenset({
        "public_field_definition", "property_signature",
    })

    def parse(self, relative_path: str, source: str):
        parsed = super().parse(relative_path, source)
        exported_names = set()
        code = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*|(['\"`])(?:\\.|(?!\1)[^\\])*?\1", " ", source)
        for match in re.finditer(r"(?m)\bexport\s*\{(?P<items>[^}]*)\}(?!\s*from\b)", code):
            for item in match.group("items").split(","):
                item = item.strip()
                if item:
                    exported_names.add(item.split(" as ")[0].strip())
        default_match = re.search(r"(?m)\bexport\s+default\s+([A-Za-z_$][\w$]*)", code)
        if default_match:
            exported_names.add(default_match.group(1))
        if not exported_names:
            return parsed
        symbols = tuple(
            replace(symbol, is_exported=True)
            if symbol.name in exported_names
            else symbol
            for symbol in parsed.symbols
        )
        return ParsedSource(symbols, parsed.imports, parsed.references)

    def _callable_value(self, node, source_bytes: bytes):
        value = node.child_by_field_name("value")
        if value is None:
            return None
        if value.type in self.function_value_types:
            return value
        if value.type == "call_expression":
            function = value.child_by_field_name("function")
            callee = self._node_text(function, source_bytes) if function is not None else ""
            if re.search(r"(?:^|\.)?(?:memo|forwardRef|observer)$", callee):
                candidate = next(
                    (child for child in self._walk(value) if child.type in self.function_value_types),
                    None,
                )
                if candidate is not None:
                    return candidate
        return None

    def _get_symbol_kind(self, node, source_bytes: bytes) -> str | None:
        if node.type == "variable_declarator":
            if self._callable_value(node, source_bytes) is not None:
                return "function"
            return "variable"
        if node.type in self.property_node_kinds:
            return "property"
        if node.type == "enum_assignment":
            return "enum_member"
        return self.symbol_node_kinds.get(node.type)

    def _is_scope_node(self, node, symbol_kind, symbol_name, source_bytes=None) -> bool:
        if not symbol_name:
            return False
        if node.type in self.class_scope_types or node.type in self.function_scope_types:
            return True
        if node.type == "variable_declarator":
            return self._callable_value(node, source_bytes or b"") is not None
        return False

    def _build_symbol(
        self, node, *, relative_path, source_bytes, scopes, symbol_name, symbol_kind
    ) -> Symbol:
        value = (
            node.child_by_field_name("value")
            if node.type == "variable_declarator"
            else None
        )
        callable_value = self._callable_value(node, source_bytes) if node.type == "variable_declarator" else None
        signature_node = callable_value or value or node
        parameters = signature_node.child_by_field_name("parameters")
        signature = (
            self._node_text(parameters, source_bytes)
            if parameters is not None
            else None
        )
        return_type_node = signature_node.child_by_field_name("return_type")
        return_type = (
            self._node_text(return_type_node, source_bytes).lstrip(": ").strip()
            if return_type_node is not None
            else None
        )
        prefix_end = (
            node.child_by_field_name("name").start_byte
            if node.child_by_field_name("name") is not None
            else node.start_byte
        )
        prefix = source_bytes[node.start_byte:prefix_end].decode("utf-8", "replace")
        modifiers = tuple(
            token for token in ("public", "private", "protected", "static", "abstract", "readonly", "declare", "default")
            if re.search(rf"\b{token}\b", prefix)
        )
        is_async = bool(re.search(r"\basync\b", prefix)) or bool(
            signature_node is not node and re.search(r"^\s*async\b", self._node_text(signature_node, source_bytes))
        )
        is_exported = self._has_export_ancestor(node) or bool(
            re.search(r"\bexport\b", prefix)
        )
        decorators = tuple(
            self._node_text(child, source_bytes)
            for child in node.named_children
            if child.type == "decorator"
        )
        role = None
        if symbol_kind in {"function", "class"} and symbol_name[:1].isupper():
            inspect_node = value or node
            if any(
                descendant.type in {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
                for descendant in self._walk(inspect_node)
            ):
                role = "react_component"
            elif node.type == "class_declaration" and "React.Component" in self._node_text(node, source_bytes):
                role = "react_component"

        return Symbol(
            name=symbol_name,
            qualified_name=".".join([*scopes, symbol_name]),
            kind=symbol_kind,
            relative_path=relative_path,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=signature,
            return_type=return_type,
            modifiers=modifiers,
            decorators=decorators,
            is_exported=is_exported,
            is_async=is_async,
            role=role,
        )

    @staticmethod
    def _has_export_ancestor(node) -> bool:
        parent = node.parent
        while parent is not None:
            if parent.type == "export_statement":
                return True
            parent = parent.parent
        return False

    def _parse_import(self, node, source_bytes: bytes) -> ImportRecord | None:
        text = self._node_text(node, source_bytes).strip()
        line = node.start_point.row + 1
        kind = "import"
        is_type_only = bool(re.match(r"(?:import|export)\s+type\b", text))
        target = None
        imported: list[str] = []

        if node.type == "import_statement":
            match = re.search(r"\bfrom\s*(['\"])(?P<target>.*?)\1", text, re.DOTALL)
            if match is None:
                match = re.match(r"import\s*(['\"])(?P<target>.*?)\1", text, re.DOTALL)
            if match is None:
                require = re.search(r"\brequire\s*\(\s*(['\"])(?P<target>.*?)\1", text, re.DOTALL)
                if require is None:
                    return None
                target = require.group("target")
                kind = "commonjs"
                alias = re.match(r"import\s+(?P<name>[A-Za-z_$][\w$]*)\s*=", text)
                if alias:
                    imported.append("default")
                return ImportRecord(target, tuple(imported), 0, line, kind, is_type_only)
            target = match.group("target")
            clause = text[len("import"):match.start()]
            clause = re.sub(r"^\s*type\s+", "", clause).strip()
            if clause:
                named_specifiers = re.search(r"\{(?P<body>[^}]*)\}", clause, re.DOTALL)
                if named_specifiers:
                    type_items = [item.strip() for item in named_specifiers.group("body").split(",") if item.strip()]
                    if type_items and all(re.match(r"type\s+", item) for item in type_items):
                        is_type_only = True
                default = clause.split(",", 1)[0].strip() if not clause.startswith(("{", "*")) else ""
                if default:
                    imported.append("default")
                named = re.search(r"\{(?P<body>[^}]*)\}", clause, re.DOTALL)
                if named:
                    for item in named.group("body").split(","):
                        item = re.sub(r"^\s*type\s+", "", item).strip()
                        if item:
                            imported.append(item.split(" as ")[0].strip())
                namespace = re.search(r"\*\s+as\s+(\w+)", clause)
                if namespace:
                    imported.append("*")
        elif node.type == "export_statement":
            match = re.search(r"\bfrom\s*(['\"])(?P<target>.*?)\1", text, re.DOTALL)
            if match is None:
                return None
            target = match.group("target")
            kind = "re-export"
            named = re.search(r"\{(?P<body>[^}]*)\}", text, re.DOTALL)
            if named:
                imported = [
                    item.strip().removeprefix("type ").split(" as ")[0].strip()
                    for item in named.group("body").split(",")
                    if item.strip()
                ]
            elif re.search(r"export\s+\*", text):
                imported = ["*"]
        elif node.type == "call_expression":
            require = re.search(r"\brequire\s*\(\s*(['\"])(?P<target>.*?)\1", text, re.DOTALL)
            dynamic = re.search(r"\bimport\s*\(\s*(['\"])(?P<target>.*?)\1", text, re.DOTALL)
            match = require or dynamic
            if match is None:
                return None
            target = match.group("target")
            kind = "commonjs" if require else "dynamic"
        else:
            return None

        if not target:
            return None
        return ImportRecord(
            module=target,
            imported_names=tuple(dict.fromkeys(imported)),
            relative_level=0,
            line=line,
            kind=kind,
            is_type_only=is_type_only,
        )

    def _extract_references(self, root, source_bytes: bytes) -> list[ReferenceRecord]:
        references = []
        for node in self._walk(root):
            if node.type not in {"jsx_opening_element", "jsx_self_closing_element"}:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                name_node = next(
                    (child for child in node.named_children if child.type in {"identifier", "member_expression", "jsx_member_expression", "jsx_identifier"}),
                    None,
                )
            if name_node is None:
                continue
            target = self._node_text(name_node, source_bytes).strip()
            if target and target[0].isupper():
                references.append(
                    ReferenceRecord(
                        target=target,
                        kind="jsx_component",
                        line=node.start_point.row + 1,
                    )
                )
        return references
