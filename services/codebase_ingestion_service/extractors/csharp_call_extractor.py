from tree_sitter import Node

from models.call import Call

from extractors.tree_sitter_call_extractor import (
    TreeSitterCallExtractor
)


class CSharpCallExtractor(
    TreeSitterCallExtractor
):

    def is_scope(
        self,
        node: Node,
    ):

        return node.type in {
            "method_declaration",
            "constructor_declaration",
            "local_function_statement",
        }

    def get_scope_name(
        self,
        node: Node,
    ):

        name = node.child_by_field_name(
            "name"
        )

        if not name:

            return None

        return self.text(name)

    def is_call(
        self,
        node: Node,
    ):

        return node.type in {
            "invocation_expression",
            "object_creation_expression",
        }

    def create_call(
        self,
        node: Node,
        current_scope,
    ):

        if not current_scope:

            return None

        # -------------------------
        # Method invocation
        # -------------------------

        if node.type == "invocation_expression":

            function = node.child_by_field_name(
                "function"
            )

            if not function:

                return None

            return Call(

                caller=current_scope,

                callee=self.text(function),

                line=node.start_point[0] + 1,

            )

        # -------------------------
        # Object creation
        # -------------------------

        if node.type == "object_creation_expression":

            type_node = node.child_by_field_name(
                "type"
            )

            if not type_node:

                return None

            return Call(

                caller=current_scope,

                callee=self.text(type_node),

                line=node.start_point[0] + 1,

            )

        return None