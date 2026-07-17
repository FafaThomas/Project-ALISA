from tree_sitter import Node

from models.symbol import Symbol

from extractors.tree_sitter_symbol_extractor import (
    TreeSitterSymbolExtractor,
)


class PythonSymbolExtractor(
    TreeSitterSymbolExtractor
):

    def is_symbol(
        self,
        node: Node,
    ):

        return node.type in {

            "function_definition",

            "class_definition",

        }

    def create_symbol(
        self,
        node: Node,
    ):

        name_node = node.child_by_field_name(
            "name"
        )

        if not name_node:

            return None

        name = name_node.text.decode()

        kind = {

            "function_definition": "function",

            "class_definition": "class",

        }[node.type]

        return Symbol(

            name=name,

            qualified_name="",

            kind=kind,

            start_line=node.start_point[0] + 1,

            end_line=node.end_point[0] + 1,

        )