"""SCSS parser extracting selectors, variables, mixins, functions and includes."""

from __future__ import annotations

import re

from coding_agent.parsers.base import ParsedSource, ReferenceRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class SCSSParser(GrammarParser):
    language = "scss"
    grammar_name = "scss"
    extensions = (".scss",)
    symbol_node_kinds = {"class_selector": "class_selector", "id_selector": "id_selector", "at_rule": "at_rule", "pseudo_class_selector": "pseudo_class_selector", "pseudo_element_selector": "pseudo_element_selector", "attribute_selector": "attribute_selector"}
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
        patterns = (
            ("scss_variable", r"(?m)(\$[A-Za-z_][\w-]*)\s*:"),
            ("mixin", r"(?m)@mixin\s+([\w-]+)"),
            ("function", r"(?m)@function\s+([\w-]+)"),
        )
        for kind, pattern in patterns:
            for match in re.finditer(pattern, code):
                name = match.group(1)
                line = source.count("\n", 0, match.start()) + 1
                symbols.append(Symbol(name, name, kind, relative_path, line, line))
        for match in re.finditer(r"(?m)@include\s+([\w-]+)", code):
            references.append(ReferenceRecord(match.group(1), "mixin_include", source.count("\n", 0, match.start()) + 1))
        symbols.sort(key=lambda item: (item.line, item.qualified_name, item.kind))
        return ParsedSource(tuple(symbols), parsed.imports, tuple(references))

    @staticmethod
    def _mask_comments_and_strings(source: str) -> str:
        pattern = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*|(['\"])(?:\\.|(?!\1)[^\\])*?\1")
        return pattern.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), source)
