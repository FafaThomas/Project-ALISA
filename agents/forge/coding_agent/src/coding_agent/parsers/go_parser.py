"""Language-specific Go structural parser."""

from __future__ import annotations

import re
from dataclasses import replace

from coding_agent.parsers.base import ImportRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class GoParser(GrammarParser):
    language = "go"
    grammar_name = "go"
    extensions = (".go",)
    symbol_node_kinds = {
        "package_clause": "package",
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
        "field_declaration": "field",
        "method_spec": "method_signature",
        "const_spec": "constant",
        "var_spec": "variable",
    }
    scope_node_kinds = frozenset({"package_clause", "type_spec"})
    import_node_kinds = frozenset({"import_spec"})

    _IMPORT = re.compile(
        r'^\s*(?:(?P<alias>\.|_|[A-Za-z_]\w*)\s+)?'
        r'"(?P<path>[^"]+)"\s*$'
    )

    def _get_symbol_name(self, node, source_bytes: bytes) -> str | None:
        if node.type == "package_clause":
            package_node = node.child_by_field_name("name")
            if package_node is None:
                package_node = next(iter(node.named_children), None)
            if package_node is not None:
                return self._node_text(package_node, source_bytes).strip()
        return super()._get_symbol_name(node, source_bytes)

    def _get_symbol_kind(self, node, source_bytes: bytes) -> str | None:
        if node.type == "type_spec":
            type_node = node.child_by_field_name("type")
            if type_node is not None:
                if type_node.type == "struct_type":
                    return "struct"
                if type_node.type == "interface_type":
                    return "interface"
                if type_node.type == "type":
                    return "type_alias"
        return super()._get_symbol_kind(node, source_bytes)

    def _parse_import(self, node, source_bytes: bytes) -> ImportRecord | None:
        text = self._node_text(node, source_bytes)
        match = self._IMPORT.match(text)
        if not match:
            return None
        alias = match.group("alias")
        return ImportRecord(
            module=match.group("path"),
            imported_names=(),
            relative_level=0,
            line=node.start_point.row + 1,
            kind="go_import",
            alias=alias,
        )

    def parse(self, relative_path: str, source: str):
        parsed = super().parse(relative_path, source)
        package_name = next(
            (symbol.name for symbol in parsed.symbols if symbol.kind == "package"),
            None,
        )
        if not package_name:
            return parsed
        return type(parsed)(
            symbols=tuple(
                symbol if symbol.kind == "package" else replace(
                    symbol, qualified_name=f"{package_name}.{symbol.qualified_name}"
                )
                for symbol in parsed.symbols
            ),
            imports=parsed.imports,
            references=parsed.references,
        )

    def _build_symbol(self, node, **kwargs) -> Symbol:
        symbol = super()._build_symbol(node, **kwargs)
        metadata = list(symbol.metadata)
        exported = bool(symbol.name and symbol.name[0].isupper())

        if node.type == "method_declaration":
            receiver = node.child_by_field_name("receiver")
            if receiver is not None:
                # Preserve the exact receiver syntax in metadata. Normalize a
                # separate copy only for extracting the receiver type used in
                # the method's qualified name.
                receiver_text = self._node_text(
                    receiver, kwargs["source_bytes"]
                ).strip()
                normalized_receiver = receiver_text.strip().strip("() ")
                receiver_match = re.search(
                    r"(?:\*\s*)?([A-Za-z_]\w*)(?:\s*\[[^]]+\])?\s*$",
                    normalized_receiver,
                )
                if receiver_match:
                    receiver_name = receiver_match.group(1)
                    metadata.append(("receiver", receiver_text))
                    symbol = Symbol(
                        **{
                            **symbol.__dict__,
                            "qualified_name": ".".join([*kwargs["scopes"], receiver_name, symbol.name]),
                        }
                    )

        type_parameters = node.child_by_field_name("type_parameters")
        if type_parameters is not None:
            metadata.append((
                "type_parameters",
                self._node_text(type_parameters, kwargs["source_bytes"]),
            ))

        return Symbol(
            **{
                **symbol.__dict__,
                "is_exported": exported,
                "metadata": tuple(metadata),
            }
        )
