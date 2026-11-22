"""HTML parser extracting element hierarchy, identifiers and asset links."""

from __future__ import annotations

import re

from coding_agent.parsers.base import ReferenceRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class HTMLParser(GrammarParser):
    language = "html"
    grammar_name = "html"
    extensions = (".html", ".htm")
    symbol_node_kinds = {"element": "element"}
    scope_node_kinds = frozenset({"element"})
    name_fields = ("name", "tag_name")

    def _extract_additional_symbols(
        self, root, *, relative_path: str, source_bytes: bytes
    ) -> list[Symbol]:
        symbols = []
        for node in self._walk(root):
            if node.type != "attribute":
                continue
            text = self._node_text(node, source_bytes).strip()
            match = re.match(
                r"(?is)^(id|class)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
                text,
            )
            if not match:
                continue
            attr = match.group(1).lower()
            value = next(group for group in match.groups()[1:] if group is not None)
            kind = "html_id" if attr == "id" else "html_class"
            for name in value.split():
                symbols.append(Symbol(
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    relative_path=relative_path,
                    line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                ))
        return symbols

    def _extract_references(self, root, source_bytes: bytes) -> list[ReferenceRecord]:
        references = []
        for node in self._walk(root):
            if node.type != "attribute":
                continue
            text = self._node_text(node, source_bytes).strip()
            match = re.match(
                r"(?is)^(?:src|href)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
                text,
            )
            if not match:
                continue
            target = next(group for group in match.groups() if group is not None).strip()
            if target and not target.startswith(("#", "data:", "javascript:")):
                references.append(ReferenceRecord(
                    target=target,
                    kind="html_asset",
                    line=node.start_point.row + 1,
                ))
        return references
