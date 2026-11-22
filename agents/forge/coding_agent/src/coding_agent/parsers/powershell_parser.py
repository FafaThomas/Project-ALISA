"""Language-specific Tree-sitter parser for powershell."""

from coding_agent.parsers.grammar_parser import GrammarParser


class PowerShellParser(GrammarParser):
    """Extract structural syntax information from powershell files."""
    language = 'powershell'
    grammar_name = 'powershell'
    extensions = ('.ps1', '.psm1', '.psd1')
    symbol_node_kinds = {'function_statement': 'function', 'class_statement': 'class', 'enum_statement': 'enum'}
    scope_node_kinds = frozenset({'class_statement'})
