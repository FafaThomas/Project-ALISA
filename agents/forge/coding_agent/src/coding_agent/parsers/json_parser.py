"""Language-specific Tree-sitter parser for json."""

from coding_agent.parsers.grammar_parser import GrammarParser


class JSONParser(GrammarParser):
    """Extract structural syntax information from json files."""
    language = 'json'
    grammar_name = 'json'
    extensions = ('.json',)
    symbol_node_kinds = {'pair': 'property'}
    name_fields = ('key',)
    scope_node_kinds = frozenset({'pair'})
