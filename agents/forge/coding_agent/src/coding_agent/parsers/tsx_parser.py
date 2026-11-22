"""Language-specific semantic parser for tsx."""

from coding_agent.parsers.javascript_family import JavaScriptFamilyParser


class TSXParser(JavaScriptFamilyParser):
    language = "tsx"
    grammar_name = "tsx"
    extensions = ('.tsx',)
    symbol_node_kinds = {'function_declaration': 'function', 'class_declaration': 'class', 'interface_declaration': 'interface', 'type_alias_declaration': 'type', 'enum_declaration': 'enum', 'method_definition': 'method', 'method_signature': 'method', 'abstract_class_declaration': 'class', 'variable_declarator': 'variable'}
    import_node_kinds = frozenset({'import_statement', 'export_statement', 'call_expression'})
