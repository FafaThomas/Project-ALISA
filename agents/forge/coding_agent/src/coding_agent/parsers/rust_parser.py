"""Language-specific Rust structural parser."""

from __future__ import annotations

import re

from coding_agent.parsers.base import ImportRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class RustParser(GrammarParser):
    language = "rust"
    grammar_name = "rust"
    extensions = (".rs",)
    symbol_node_kinds = {
        "function_item": "function",
        "function_signature_item": "method_signature",
        "struct_item": "struct",
        "enum_item": "enum",
        "enum_variant": "variant",
        "trait_item": "trait",
        "impl_item": "implementation",
        "mod_item": "module",
        "type_item": "type",
        "const_item": "constant",
        "static_item": "static",
        "macro_definition": "macro",
        "field_declaration": "field",
    }
    scope_node_kinds = frozenset({
        "mod_item", "trait_item", "impl_item", "struct_item", "enum_item"
    })
    import_node_kinds = frozenset({"use_declaration"})

    def _get_symbol_name(self, node, source_bytes: bytes) -> str | None:
        if node.type == "impl_item":
            text = self._node_text(node, source_bytes)
            match = re.search(
                r"\bimpl\s+(?:<[^>]*>\s*)?(?:(?P<trait>[\w:]+(?:<[^>]+>)?)\s+for\s+)?(?P<type>[\w:]+(?:<[^>]+>)?)",
                text,
            )
            if match:
                return match.group("type")
        return super()._get_symbol_name(node, source_bytes)

    @staticmethod
    def _split_use_items(value: str) -> tuple[str, ...]:
        items = []
        depth = 0
        start = 0
        for index, char in enumerate(value):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            elif char == "," and depth == 0:
                items.append(value[start:index].strip())
                start = index + 1
        tail = value[start:].strip()
        if tail:
            items.append(tail)
        return tuple(item for item in items if item)

    @staticmethod
    def _split_use_tree_items(value: str) -> tuple[str, ...]:
        items = []
        depth = 0
        start = 0
        for index, char in enumerate(value):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            elif char == "," and depth == 0:
                items.append(value[start:index].strip())
                start = index + 1
        tail = value[start:].strip()
        if tail:
            items.append(tail)
        return tuple(item for item in items if item)

    def _parse_imports(self, node, source_bytes: bytes) -> tuple[ImportRecord, ...]:
        text = self._node_text(node, source_bytes).strip()
        is_public = bool(re.match(r"^pub(?:\([^)]*\))?\s+use\b", text))
        text = re.sub(r"^pub(?:\([^)]*\))?\s+", "", text)
        text = re.sub(r"^use\s+", "", text).rstrip(";").strip()
        if not text:
            return ()

        line = node.start_point.row + 1
        records: list[ImportRecord] = []

        def split_group(expression: str):
            depth = 0
            opening = None
            for index, char in enumerate(expression):
                if char == "{":
                    if depth == 0:
                        opening = index
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0 and index == len(expression) - 1 and opening is not None:
                        return expression[:opening].rstrip(": "), expression[opening + 1:index]
            return None

        def emit_leaf(expression: str, prefix: str):
            expression = expression.strip()
            alias = None
            alias_match = re.search(r"\s+as\s+(\w+)\s*$", expression)
            if alias_match:
                alias = alias_match.group(1)
                expression = expression[:alias_match.start()].strip()
            if expression == "*":
                combined = prefix
                module = combined or None
                names = ()
                wildcard = True
            elif expression.endswith("::*"):
                combined = "::".join(part for part in (prefix, expression[:-3]) if part)
                module = combined or None
                names = ()
                wildcard = True
            else:
                combined = "::".join(part for part in (prefix, expression) if part)
                combined = combined.removeprefix("::")
                if expression == "self":
                    module = prefix or None
                    names = ("self",)
                    wildcard = False
                elif "::" in combined:
                    module, leaf = combined.rsplit("::", 1)
                    names = (leaf,)
                    wildcard = False
                else:
                    module = combined or None
                    names = ()
                    wildcard = False
            aliases = ((names[0], alias),) if alias and names else ()
            records.append(ImportRecord(
                module=module,
                imported_names=names,
                relative_level=0,
                line=line,
                kind="rust_use",
                alias=alias,
                is_wildcard=wildcard,
                is_public=is_public,
                aliases=aliases,
            ))

        def expand(expression: str, prefix: str = ""):
            expression = expression.strip()
            group = split_group(expression)
            if group is not None:
                head, body = group
                combined_prefix = "::".join(part for part in (prefix, head) if part)
                for item in self._split_use_tree_items(body):
                    expand(item, combined_prefix)
                return
            emit_leaf(expression, prefix)

        expand(text)
        return tuple(records)

    def _parse_import(self, node, source_bytes: bytes) -> ImportRecord | None:
        text = self._node_text(node, source_bytes).strip()
        is_public = bool(re.match(r"^pub(?:\([^)]*\))?\s+use\b", text))
        text = re.sub(r"^pub(?:\([^)]*\))?\s+", "", text)
        text = re.sub(r"^use\s+", "", text)
        text = text.rstrip(";").strip()
        if not text:
            return None
        alias_match = re.search(r"\s+as\s+(\w+)\s*$", text)
        alias = alias_match.group(1) if alias_match else None
        if alias_match:
            text = text[:alias_match.start()].strip()
        wildcard = text.endswith("::*")
        if wildcard:
            text = text[:-3]
        names = ()
        aliases = []
        module = text
        if "{" in text and text.endswith("}"):
            prefix, body = text.split("{", 1)
            module = prefix.rstrip(":")
            parsed_names = []
            for item in self._split_use_items(body[:-1]):
                original, separator, renamed = item.partition(" as ")
                original = original.strip()
                if original:
                    parsed_names.append(original)
                    if separator and renamed.strip():
                        aliases.append((original, renamed.strip()))
            names = tuple(parsed_names)
        elif "::" in text and not wildcard:
            module, leaf = text.rsplit("::", 1)
            names = (leaf,)
        return ImportRecord(
            module=module or None,
            imported_names=names,
            relative_level=0,
            line=node.start_point.row + 1,
            kind="rust_use",
            alias=alias,
            is_wildcard=wildcard,
            is_public=is_public,
            aliases=tuple(aliases),
        )

    def _build_symbol(self, node, **kwargs) -> Symbol:
        symbol = super()._build_symbol(node, **kwargs)
        text = self._node_text(node, kwargs["source_bytes"])
        header = text.split("{", 1)[0]
        metadata = list(symbol.metadata)
        visibility = re.search(r"\bpub(?:\(([^)]*)\))?", header)
        if visibility:
            metadata.append(("visibility", visibility.group(1) or "pub"))
        if node.type == "impl_item":
            trait_match = re.search(
                r"\bimpl\s+(?:<[^>]*>\s*)?([\w:]+(?:<[^>]+>)?)\s+for\s+",
                header,
            )
            if trait_match:
                metadata.append(("implements", trait_match.group(1)))
        if node.type == "mod_item":
            # Rust distinguishes inline modules from out-of-line declarations.
            # Preserve this syntax so the later resolver can locate module files.
            module_form = "inline" if re.search(r"\bmod\s+[A-Za-z_]\w*\s*\{", text) else "external"
            metadata.append(("module_form", module_form))
        if node.type in {"struct_item", "enum_item", "trait_item", "function_item"} and not visibility:
            metadata.append(("visibility", "private"))
        type_parameters = node.child_by_field_name("type_parameters")
        if type_parameters is not None:
            metadata.append((
                "type_parameters",
                self._node_text(type_parameters, kwargs["source_bytes"]),
            ))
        # Rust outer attributes (#[...]) are sibling nodes immediately
        # preceding the item they decorate, not descendants of the item.
        # Collect the contiguous attribute siblings before this declaration.
        attributes_list = []
        parent = getattr(node, "parent", None)
        if parent is not None:
            siblings = list(parent.named_children)
            node_index = next(
                (
                    index for index, sibling in enumerate(siblings)
                    if sibling.start_byte == node.start_byte
                ),
                None,
            )
            if node_index is not None:
                for sibling in reversed(siblings[:node_index]):
                    if sibling.type != "attribute_item":
                        break
                    attributes_list.append(
                        self._node_text(sibling, kwargs["source_bytes"])
                    )
        attributes = tuple(reversed(attributes_list))
        return Symbol(
            **{
                **symbol.__dict__,
                "is_exported": bool(visibility and visibility.group(1) is None),
                "decorators": attributes,
                "metadata": tuple(metadata),
            }
        )
