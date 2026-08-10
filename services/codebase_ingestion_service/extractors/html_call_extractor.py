from tree_sitter import Node

from models.call import Call

from extractors.tree_sitter_call_extractor import (
    TreeSitterCallExtractor,
)


class HTMLCallExtractor(
    TreeSitterCallExtractor
):

    EVENT_ATTRIBUTES = {
        "onclick",
        "ondblclick",
        "onchange",
        "onsubmit",
        "onload",
        "oninput",
        "onkeydown",
        "onkeyup",
        "onkeypress",
        "onfocus",
        "onblur",
        "onmouseover",
        "onmouseout",
        "onmouseenter",
        "onmouseleave",
        "onmousedown",
        "onmouseup",
        "ontouchstart",
        "ontouchend",
    }

    def is_scope(
        self,
        node: Node,
    ):

        return node.type in {
            "script_element",
        }

    def get_scope_name(
        self,
        node: Node,
    ):

        return "inline_script"

    def is_call(
        self,
        node: Node,
    ):

        return node.type == "call_expression"

    def create_call(
        self,
        node: Node,
        current_scope,
    ):

        if not current_scope:
            return None

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

    def extract(
        self,
        tree,
    ):

        calls = []

        self.walk_html(
            tree.root_node,
            calls,
        )

        return calls

    def walk_html(
        self,
        node: Node,
        calls: list,
    ):

        # ---------------------------------
        # Event handler attributes
        # ---------------------------------

        if node.type == "attribute":

            name_node = node.child_by_field_name(
                "name"
            )

            value_node = node.child_by_field_name(
                "value"
            )

            if name_node and value_node:

                name = self.text(
                    name_node
                )

                if name.lower() in self.EVENT_ATTRIBUTES:

                    self.extract_event_calls(
                        value_node,
                        calls,
                    )

        # ---------------------------------
        # Inline script
        # ---------------------------------

        if node.type == "script_element":

            self.extract_script_calls(
                node,
                calls,
            )

        for child in node.children:

            self.walk_html(
                child,
                calls,
            )

    def extract_event_calls(
        self,
        value_node: Node,
        calls: list,
    ):

        text = self.text(
            value_node
        )

        # We need a JavaScript tree to
        # reliably identify calls inside
        # event-handler expressions.
        #
        # This first pass extracts the
        # handler expression itself.

        calls.append(
            Call(
                caller="html_event",
                callee=text,
                line=value_node.start_point[0] + 1,
            )
        )

    def extract_script_calls(
        self,
        node: Node,
        calls: list,
    ):

        # The HTML grammar contains the
        # script contents, but the contents
        # need to be parsed as JavaScript
        # to reliably identify call_expression
        # nodes.
        #
        # For now we don't guess at the
        # embedded JavaScript AST.

        return