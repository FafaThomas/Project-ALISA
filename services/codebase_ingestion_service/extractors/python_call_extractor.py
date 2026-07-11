from tree_sitter import Node

from extractors.base_call_extractor import BaseCallExtractor

from models.call import Call


class PythonCallExtractor(BaseCallExtractor):

    def extract(self, parse_result):

        self.source = parse_result.source.raw_source

        calls = []

        self.walk(
            parse_result.tree.root_node,
            None,
            calls
        )

        return calls

    def walk(
        self,
        node: Node,
        current_function,
        calls
    ):

        if node.type == "function_definition":

            name = node.child_by_field_name("name")

            current_function = self.text(name)

        elif node.type == "call":

            func = node.child_by_field_name("function")

            if current_function and func:

                calls.append(
                    Call(
                        caller=current_function,
                        callee=self.text(func),
                        line=node.start_point[0] + 1
                    )
                )

        for child in node.children:

            self.walk(
                child,
                current_function,
                calls
            )

    def text(self, node):

        return self.source[
            node.start_byte:node.end_byte
        ]