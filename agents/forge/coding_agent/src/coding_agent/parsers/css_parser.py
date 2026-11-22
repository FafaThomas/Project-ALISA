"""CSS parser extracting selectors, custom properties and imports."""

from __future__ import annotations

import re

from coding_agent.parsers.base import ParsedSource, ReferenceRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class CSSParser(GrammarParser):
    language = "css"
    grammar_name = "css"
    extensions = (".css",)
    symbol_node_kinds = {"class_selector": "class_selector", "id_selector": "id_selector", "tag_name": "element_selector", "at_rule": "at_rule", "pseudo_class_selector": "pseudo_class_selector", "pseudo_element_selector": "pseudo_element_selector", "attribute_selector": "attribute_selector"}
    import_node_kinds = frozenset({"import_statement"})
    text_name_node_kinds = frozenset({"class_selector", "id_selector"})
    import_pattern = r"@import\s+(?:url\()?['\"]?(?P<target>[^'\")\s;]+)"

    def parse(self, relative_path: str, source: str) -> ParsedSource:
        parsed = super().parse(relative_path, source)
        symbols = list(parsed.symbols)
        references = list(parsed.references)
        references.extend(
            ReferenceRecord(item.module, "stylesheet_import", item.line)
            for item in parsed.imports
            if item.module
        )
        code = self._mask_comments_and_strings(source)
        for match in re.finditer(r"(?m)(--[A-Za-z_][\w-]*)\s*:", code):
            line = source.count("\n", 0, match.start()) + 1
            symbols.append(Symbol(match.group(1), match.group(1), "custom_property", relative_path, line, line))
        symbols.sort(key=lambda item: (item.line, item.qualified_name, item.kind))
        return ParsedSource(tuple(symbols), parsed.imports, tuple(references))

    @staticmethod
    def _mask_comments_and_strings(source: str) -> str:
        pattern = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*|(['\"])(?:\\.|(?!\1)[^\\])*?\1")
        return pattern.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), source)
