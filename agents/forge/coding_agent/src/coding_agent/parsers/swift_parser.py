"""Language-specific Tree-sitter parser for swift."""

from coding_agent.parsers.grammar_parser import GrammarParser


class SwiftParser(GrammarParser):
    """Extract structural syntax information from swift files."""
    language = 'swift'
    grammar_name = 'swift'
    extensions = ('.swift',)
    symbol_node_kinds = {'class_declaration': 'class', 'struct_declaration': 'struct', 'enum_declaration': 'enum', 'protocol_declaration': 'protocol', 'function_declaration': 'function', 'init_declaration': 'initializer'}
    scope_node_kinds = frozenset({'class_declaration', 'enum_declaration', 'protocol_declaration', 'struct_declaration'})
