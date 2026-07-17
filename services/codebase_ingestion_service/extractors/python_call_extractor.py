from tree_sitter import Node

from models.call import Call

from extractors.tree_sitter_call_extractor import (
    TreeSitterCallExtractor,
)


class PythonCallExtractor(
    TreeSitterCallExtractor
):

    def is_scope(
        self,
        node: Node,
    ):

        return node.type == "function_definition"

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

        return node.type == "call"

    def create_call(
        self,
        node: Node,
        current_scope,
    ):

        if not current_scope:

            return None

        func = node.child_by_field_name(
            "function"
        )

        if not func:

            return None

        return Call(

            caller=current_scope,

            callee=self.text(func),

            line=node.start_point[0] + 1,

        )