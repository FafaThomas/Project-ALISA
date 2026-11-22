"""Shared configurable Tree-sitter adapter used by language parser modules.

Language-specific node mappings and import rules live in their own modules.
This class owns only common traversal and record-construction mechanics.
"""

from __future__ import annotations

import re
from typing import ClassVar

from coding_agent.parsers.base import ImportRecord
from coding_agent.parsers.treesitter_parser import TreeSitterParser


class GrammarParser(TreeSitterParser):
    """Base class for grammar-backed language-specific parsers."""

    filenames: ClassVar[tuple[str, ...]] = ()
    name_fields: ClassVar[tuple[str, ...]] = ("name", "key", "package")
    text_name_node_kinds: ClassVar[frozenset[str]] = frozenset()
    import_pattern: ClassVar[str | None] = None

    def _get_symbol_name(self, node, source_bytes: bytes) -> str | None:
        for field_name in self.name_fields:
            name_node = node.child_by_field_name(field_name)
            if name_node is not None:
                value = self._node_text(name_node, source_bytes).strip()
                if value:
                    return value.strip("\"'` ")

        if node.type in self.text_name_node_kinds:
            value = self._node_text(node, source_bytes).strip()
            return value.lstrip(".#").strip() or None

        preferred = {
            "identifier", "type_identifier", "field_identifier",
            "namespace_identifier", "property_identifier",
            "tag_name", "simple_identifier", "bare_key",
        }

        def find_identifier(candidate):
            if candidate.type in preferred:
                return self._node_text(candidate, source_bytes).strip("\"'` ")
            for child in candidate.named_children:
                found = find_identifier(child)
                if found:
                    return found
            return None

        if node.type == "element":
            for child in node.named_children:
                for descendant in self._walk(child):
                    if descendant.type == "tag_name":
                        return self._node_text(descendant, source_bytes).strip()

        return find_identifier(node)

    def _parse_import(self, node, source_bytes: bytes) -> ImportRecord | None:
        if self.import_pattern is None:
            return None

        match = re.search(
            self.import_pattern,
            self._node_text(node, source_bytes),
            re.MULTILINE | re.DOTALL,
        )
        if match is None:
            return None

        groups = match.groupdict()
        target = groups.get("target") or groups.get("target_alt")
        if not target:
            return None

        return ImportRecord(
            module=target.strip("\"'<>` "),
            imported_names=(),
            relative_level=0,
            line=node.start_point.row + 1,
        )
