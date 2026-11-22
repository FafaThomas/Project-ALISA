"""Language-specific semantic parser for javascript."""

from coding_agent.parsers.javascript_family import JavaScriptFamilyParser


class JavaScriptParser(JavaScriptFamilyParser):
    language = "javascript"
    grammar_name = "javascript"
    extensions = ('.js', '.mjs', '.cjs')
    symbol_node_kinds = {'function_declaration': 'function', 'generator_function_declaration': 'function', 'class_declaration': 'class', 'method_definition': 'method', 'variable_declarator': 'variable'}
    import_node_kinds = frozenset({'import_statement', 'export_statement', 'call_expression'})
