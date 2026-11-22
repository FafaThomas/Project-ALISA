"""Language-specific Tree-sitter parser for yaml."""

from coding_agent.parsers.grammar_parser import GrammarParser


class YAMLParser(GrammarParser):
    """Extract structural syntax information from yaml files."""
    language = 'yaml'
    grammar_name = 'yaml'
    extensions = ('.yaml', '.yml')
    symbol_node_kinds = {'block_mapping_pair': 'property'}
    name_fields = ('key',)
    scope_node_kinds = frozenset({'block_mapping_pair'})
