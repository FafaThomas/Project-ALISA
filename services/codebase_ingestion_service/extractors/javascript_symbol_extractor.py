from tree_sitter import Node

from models.symbol import Symbol

from extractors.tree_sitter_symbol_extractor import (
    TreeSitterSymbolExtractor,
)


class JavaScriptSymbolExtractor(
    TreeSitterSymbolExtractor
):

    def is_symbol(
        self,
        node: Node,
    ):

        return node.type in {

            "function_declaration",

            "function_expression",

            "arrow_function",

            "class_declaration",

            "method_definition",

        }

    def create_symbol(
        self,
        node: Node,
    ):

        # -------------------------
        # Class
        # -------------------------

        if node.type == "class_declaration":

            name_node = node.child_by_field_name(
                "name"
            )

            if not name_node:
                return None

            return Symbol(

                name=name_node.text.decode(),

                qualified_name="",

                kind="class",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        # -------------------------
        # Method
        # -------------------------

        if node.type == "method_definition":

            name_node = node.child_by_field_name(
                "name"
            )

            if not name_node:
                return None

            return Symbol(

                name=name_node.text.decode(),

                qualified_name="",

                kind="method",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        # -------------------------
        # Function
        # -------------------------

        if node.type == "function_declaration":

            name_node = node.child_by_field_name(
                "name"
            )

            if not name_node:
                return None

            return Symbol(

                name=name_node.text.decode(),

                qualified_name="",

                kind="function",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        # -------------------------
        # Function expression
        # -------------------------

        if node.type == "function_expression":

            name_node = node.child_by_field_name(
                "name"
            )

            if not name_node:
                return None

            return Symbol(

                name=name_node.text.decode(),

                qualified_name="",

                kind="function",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        # -------------------------
        # Arrow function
        # -------------------------

        if node.type == "arrow_function":

            return Symbol(

                name="anonymous",

                qualified_name="",

                kind="function",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        return None