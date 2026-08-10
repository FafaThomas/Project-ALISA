from tree_sitter import Node

from models.symbol import Symbol

from extractors.tree_sitter_symbol_extractor import (
    TreeSitterSymbolExtractor,
)


class CSSSymbolExtractor(
    TreeSitterSymbolExtractor
):

    def is_symbol(
        self,
        node: Node,
    ):

        return node.type in {
            "rule_set",
            "media_statement",
            "keyframes_statement",
            "import_statement",
        }

    def create_symbol(
        self,
        node: Node,
    ):

        if node.type == "rule_set":

            selector = node.child_by_field_name(
                "selector"
            )

            if not selector:
                return None

            name = selector.text.decode(
                "utf-8"
            ).strip()

            return Symbol(

                name=name,

                qualified_name="",

                kind="css_rule",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        if node.type == "media_statement":

            return Symbol(

                name="@media",

                qualified_name="",

                kind="css_media",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        if node.type == "keyframes_statement":

            name_node = node.child_by_field_name(
                "name"
            )

            name = (

                name_node.text.decode(
                    "utf-8"
                )

                if name_node

                else "@keyframes"

            )

            return Symbol(

                name=name,

                qualified_name="",

                kind="css_keyframes",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        if node.type == "import_statement":

            return Symbol(

                name="@import",

                qualified_name="",

                kind="css_import",

                start_line=node.start_point[0] + 1,

                end_line=node.end_point[0] + 1,

            )

        return None