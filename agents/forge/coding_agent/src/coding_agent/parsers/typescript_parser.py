"""Language-specific semantic parser for typescript."""

from coding_agent.parsers.javascript_family import JavaScriptFamilyParser


class TypeScriptParser(JavaScriptFamilyParser):
    language = "typescript"
    grammar_name = "typescript"
    extensions = ('.ts', '.mts', '.cts')
    symbol_node_kinds = {'function_declaration': 'function', 'class_declaration': 'class', 'abstract_class_declaration': 'class', 'interface_declaration': 'interface', 'type_alias_declaration': 'type', 'enum_declaration': 'enum', 'method_definition': 'method', 'method_signature': 'method', 'variable_declarator': 'variable'}
    import_node_kinds = frozenset({'import_statement', 'export_statement', 'call_expression'})
