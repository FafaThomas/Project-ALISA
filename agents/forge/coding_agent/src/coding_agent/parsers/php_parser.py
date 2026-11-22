"""Language-specific Tree-sitter parser for php."""

from coding_agent.parsers.grammar_parser import GrammarParser


class PHPParser(GrammarParser):
    """Extract structural syntax information from php files."""
    language = 'php'
    grammar_name = 'php'
    extensions = ('.php', '.phtml')
    symbol_node_kinds = {'function_definition': 'function', 'class_declaration': 'class', 'interface_declaration': 'interface', 'trait_declaration': 'trait', 'method_declaration': 'method', 'namespace_definition': 'namespace', 'enum_declaration': 'enum'}
    scope_node_kinds = frozenset({'class_declaration', 'interface_declaration', 'trait_declaration'})
    import_node_kinds = frozenset({'namespace_use_declaration'})
    import_pattern = '(?:use|require|include)(?:_once)?\\s+(?P<target>[^;]+);'
