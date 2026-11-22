"""Language-specific Tree-sitter parser for toml."""

from coding_agent.parsers.grammar_parser import GrammarParser


class TOMLParser(GrammarParser):
    """Extract structural syntax information from toml files."""
    language = 'toml'
    grammar_name = 'toml'
    extensions = ('.toml',)
    symbol_node_kinds = {'pair': 'property', 'table': 'table', 'table_array_element': 'table_array'}
    name_fields = ('key', 'name', 'path')
    scope_node_kinds = frozenset({'table', 'table_array_element', 'pair'})
