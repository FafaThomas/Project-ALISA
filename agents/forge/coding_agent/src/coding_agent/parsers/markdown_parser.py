"""Language-specific Tree-sitter parser for markdown."""

import re

from coding_agent.parsers.base import ReferenceRecord, Symbol
from coding_agent.parsers.grammar_parser import GrammarParser


class MarkdownParser(GrammarParser):
    """Extract structural syntax information from markdown files."""
    language = 'markdown'
    grammar_name = 'markdown'
    extensions = ('.md', '.markdown')
    symbol_node_kinds = {'atx_heading': 'heading', 'setext_heading': 'heading'}
    name_fields = ('heading_content', 'content')
    text_name_node_kinds = frozenset({'atx_heading', 'setext_heading'})

    def _extract_additional_symbols(self, root, *, relative_path: str, source_bytes: bytes):
        source = source_bytes.decode("utf-8", "replace")
        symbols = []
        pattern = re.compile(r"(?ms)^```(?P<language>[^\s`\n]*)[^\n]*\n(?P<body>.*?)^```[ \t]*$")
        for match in pattern.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            end_line = source.count("\n", 0, match.end()) + 1
            language = match.group("language") or "text"
            symbols.append(Symbol(
                name=f"code_block_{line}",
                qualified_name=f"code_block_{line}",
                kind="code_block",
                relative_path=relative_path,
                line=line,
                end_line=end_line,
                declared_type=language,
            ))
        return symbols

    def _extract_references(self, root, source_bytes: bytes):
        source = source_bytes.decode("utf-8", "replace")
        references = []
        # Inline links and images.
        for match in re.finditer(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))", source):
            target = match.group(1) or match.group(2)
            if target:
                kind = "markdown_image" if match.group(0).startswith("!") else "markdown_link"
                references.append(ReferenceRecord(target, kind, source.count("\n", 0, match.start()) + 1))
        # Reference definitions: [label]: destination
        for match in re.finditer(r"(?m)^\s*\[[^]]+\]:\s*<?([^\s>]+)>?", source):
            references.append(ReferenceRecord(
                match.group(1), "markdown_reference", source.count("\n", 0, match.start()) + 1
            ))
        return references
