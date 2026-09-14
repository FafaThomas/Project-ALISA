from tree_sitter import Node

from models.call import Call

from extractors.tree_sitter_call_extractor import (
    TreeSitterCallExtractor,
)

from extractors.embedded_language_parser import (
    EmbeddedLanguageParser,
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

    def __init__(self):

        super().__init__()

        self.embedded_parser = (
            EmbeddedLanguageParser()
        )

    def is_scope(
        self,
        node: Node,
    ):

        return False

    def get_scope_name(
        self,
        node: Node,
    ):

        return None

    def is_call(
        self,
        node: Node,
    ):

        return False

    def create_call(
        self,
        node: Node,
        current_scope,
    ):

        return None

    def extract(
        self,
        parse_result,
    ):

        calls = []

        self.walk_html(
            parse_result.tree.root_node,
            calls,
        )

        return calls

    def walk_html(
        self,
        node: Node,
        calls: list,
    ):

        # ------------------------------
        # Inline event handler
        # ------------------------------

        if node.type == "attribute":

            self.extract_event_handler(
                node,
                calls,
            )

        # ------------------------------
        # Inline <script>
        # ------------------------------

        elif node.type == "script_element":

            self.extract_script_element(
                node,
                calls,
            )

        for child in node.children:

            self.walk_html(
                child,
                calls,
            )

    def extract_event_handler(
        self,
        node: Node,
        calls: list,
    ):

        name_node = node.child_by_field_name(
            "name"
        )

        value_node = node.child_by_field_name(
            "value"
        )

        if not name_node or not value_node:
            return

        name = name_node.text.decode(
            "utf-8"
        )

        if name not in self.EVENT_ATTRIBUTES:
            return

        source = value_node.text.decode(
            "utf-8"
        ).strip()

        self.extract_javascript_calls(
            source,
            value_node.start_point[0],
            calls,
        )

    def extract_script_element(
        self,
        node: Node,
        calls: list,
    ):

        source_node = None

        for child in node.children:

            if child.type in {
                "raw_text",
                "script",
            }:

                source_node = child
                break

        if not source_node:
            return

        source = source_node.text.decode(
            "utf-8"
        )

        self.extract_javascript_calls(
            source,
            source_node.start_point[0],
            calls,
        )

    def extract_javascript_calls(
        self,
        source: str,
        starting_line: int,
        calls: list,
    ):

        tree = self.embedded_parser.parse(
            "javascript",
            source,
        )

        if tree is None:
            return

        self.walk_javascript_calls(
            tree.root_node,
            starting_line,
            calls,
        )

    def walk_javascript_calls(
        self,
        node: Node,
        starting_line: int,
        calls: list,
    ):

        if node.type == "call_expression":

            function = node.child_by_field_name(
                "function"
            )

            if function:

                calls.append(
                    Call(
                        caller="html",
                        callee=function.text.decode(
                            "utf-8"
                        ),
                        line=(
                            starting_line
                            + node.start_point[0]
                            + 1
                        ),
                    )
                )

        for child in node.children:

            self.walk_javascript_calls(
                child,
                starting_line,
                calls,
            )