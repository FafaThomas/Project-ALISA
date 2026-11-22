"""Language-specific Tree-sitter parser for bash."""

from coding_agent.parsers.grammar_parser import GrammarParser


class BashParser(GrammarParser):
    """Extract structural syntax information from bash files."""
    language = 'bash'
    grammar_name = 'bash'
    extensions = ('.sh', '.bash')
    symbol_node_kinds = {'function_definition': 'function'}
    scope_node_kinds = frozenset({})
