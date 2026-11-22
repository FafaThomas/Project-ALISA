"""Language-specific Tree-sitter parser for terraform."""

from coding_agent.parsers.grammar_parser import GrammarParser


class TerraformParser(GrammarParser):
    """Extract structural syntax information from terraform files."""
    language = 'terraform'
    grammar_name = 'terraform'
    extensions = ('.tf', '.tfvars')
    symbol_node_kinds = {'block': 'block', 'attribute': 'attribute'}
    name_fields = ('name', 'key', 'type')
    scope_node_kinds = frozenset({'block'})
