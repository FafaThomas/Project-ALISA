"""Language-specific Tree-sitter parser for kotlin."""

from coding_agent.parsers.grammar_parser import GrammarParser


class KotlinParser(GrammarParser):
    """Extract structural syntax information from kotlin files."""
    language = 'kotlin'
    grammar_name = 'kotlin'
    extensions = ('.kt', '.kts')
    symbol_node_kinds = {'class_declaration': 'class', 'object_declaration': 'object', 'function_declaration': 'function', 'property_declaration': 'property', 'type_alias': 'type'}
    scope_node_kinds = frozenset({'class_declaration', 'object_declaration'})
