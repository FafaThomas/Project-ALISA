from tree_sitter import Node

from models.symbol import Symbol

from extractors.tree_sitter_symbol_extractor import (
    TreeSitterSymbolExtractor,
)


class HTMLSymbolExtractor(
    TreeSitterSymbolExtractor
):

    def is_symbol(
        self,
        node: Node,
    ):

        return node.type in {
            "element",
            "script_element",
            "style_element",
        }

    def create_symbol(
        self,
        node: Node,
    ):

        if node.type == "element":

            name_node = node.child_by_field_name(
                "tag_name"
            )

            if not name_node:
                return None

            name = name_node.text.decode(
                "utf-8"
            )

            return Symbol(

                name=name,

                qualified_name="",

                kind="html_element",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        if node.type == "script_element":

            return Symbol(

                name="script",

                qualified_name="",

                kind="script",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        if node.type == "style_element":

            return Symbol(

                name="style",

                qualified_name="",

                kind="style",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        return None