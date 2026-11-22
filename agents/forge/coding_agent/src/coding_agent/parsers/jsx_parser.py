"""Language-specific semantic parser for jsx."""

from coding_agent.parsers.javascript_family import JavaScriptFamilyParser


class JSXParser(JavaScriptFamilyParser):
    language = "jsx"
    grammar_name = "javascript"
    extensions = ('.jsx',)
    symbol_node_kinds = {'function_declaration': 'function', 'generator_function_declaration': 'function', 'class_declaration': 'class', 'method_definition': 'method', 'variable_declarator': 'variable'}
    import_node_kinds = frozenset({'import_statement', 'export_statement', 'call_expression'})
