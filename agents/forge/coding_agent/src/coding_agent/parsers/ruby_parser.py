"""Language-specific Tree-sitter parser for ruby."""

from coding_agent.parsers.grammar_parser import GrammarParser


class RubyParser(GrammarParser):
    """Extract structural syntax information from ruby files."""
    language = 'ruby'
    grammar_name = 'ruby'
    extensions = ('.rb',)
    symbol_node_kinds = {'class': 'class', 'module': 'module', 'method': 'method', 'singleton_method': 'method'}
    scope_node_kinds = frozenset({'class', 'module'})
    import_node_kinds = frozenset({'call'})
    import_pattern = 'require(?:_relative)?\\s+[\'"](?P<target>[^\'"]+)[\'"]'
