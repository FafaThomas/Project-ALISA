from tree_sitter import Node

from models.call import Call

from extractors.tree_sitter_call_extractor import (
    TreeSitterCallExtractor,
)


class JavaScriptCallExtractor(
    TreeSitterCallExtractor
):

    def is_scope(
        self,
        node: Node,
    ):

        return node.type in {

            "function_declaration",

            "function_expression",

            "arrow_function",

            "method_definition",

        }

    def get_scope_name(
        self,
        node: Node,
    ):

        name = node.child_by_field_name(
            "name"
        )

        if name:

            return self.text(name)

        return "anonymous"

    def is_call(
        self,
        node: Node,
    ):

        return node.type in {

            "call_expression",

            "new_expression",

        }

    def create_call(
        self,
        node: Node,
        current_scope,
    ):

        if not current_scope:

            return None

        # -------------------------
        # Normal function call
        # -------------------------

        if node.type == "call_expression":

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
        # new Foo()
        # -------------------------

        if node.type == "new_expression":

            constructor = node.child_by_field_name(
                "constructor"
            )

            if not constructor:

                return None

            return Call(

                caller=current_scope,

                callee=self.text(constructor),

                line=node.start_point[0] + 1,

            )

        return None