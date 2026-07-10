from tree_sitter import Node

from models.symbol import Symbol

from extractors.base_extractor import BaseExtractor


class PythonSymbolExtractor(BaseExtractor):

    def extract(self, tree):

        symbols = []

        self.walk(tree.root_node, symbols)

        return symbols

    def walk(self, node: Node, symbols):

        if node.type == "function_definition":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbols.append(
                    Symbol(
                        name=name_node.text.decode(),
                        kind="function",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                    )
                )

        elif node.type == "class_definition":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbols.append(
                    Symbol(
                        name=name_node.text.decode(),
                        kind="class",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                    )
                )

        for child in node.children:
            self.walk(child, symbols)