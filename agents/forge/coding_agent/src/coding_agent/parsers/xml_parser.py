"""Language-specific Tree-sitter parser for xml."""

from coding_agent.parsers.grammar_parser import GrammarParser


class XMLParser(GrammarParser):
    """Extract structural syntax information from xml files."""
    language = 'xml'
    grammar_name = 'xml'
    extensions = ('.xml', '.xsd', '.xsl', '.svg')
    symbol_node_kinds = {'element': 'element', 'attribute': 'attribute'}
    scope_node_kinds = frozenset({'element'})
    name_fields = ('name', 'tag_name', 'attribute_name')
