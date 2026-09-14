from tree_sitter import Node

from models.chunk import Chunk

from extractors.tree_sitter_chunk_extractor import (
    TreeSitterChunkExtractor,
)


class JavaScriptChunkExtractor(
    TreeSitterChunkExtractor
):

    MAX_TYPE_CHARS = 6000

    TYPE_NODES = {
        "class_declaration",
    }

    CHUNK_NODES = {
        "function_declaration",
        "function_expression",
        "arrow_function",
        "class_declaration",
        "method_definition",
    }

    def is_chunk(
        self,
        node: Node,
    ):

        return node.type in self.CHUNK_NODES

    def create_chunk(
        self,
        node: Node,
        lines,
    ):

        start = node.start_point[0] + 1

        end = node.end_point[0] + 1

        text = "\n".join(
            lines[start - 1:end]
        )

        if not text.strip():
            return None

        if (
            node.type in self.TYPE_NODES
            and len(text) > self.MAX_TYPE_CHARS
        ):

            text = self.create_class_summary(
                node
            )

        return Chunk(

            id=f"{start}:{end}",

            type=node.type,

            name=self.extract_name(
                node
            ),

            start_line=start,

            end_line=end,

            content=text,

        )

    def create_class_summary(
        self,
        node: Node,
    ):

        name = self.extract_name(
            node
        )

        summary = [

            f"class: {name}",

            "",

            "Members:",

        ]

        for child in node.children:

            if child.type in {
                "method_definition",
                "field_definition",
                "class_declaration",
            }:

                member_name = (
                    self.extract_name(
                        child
                    )
                )

                start = (
                    child.start_point[0] + 1
                )

                end = (
                    child.end_point[0] + 1
                )

                summary.append(

                    f"- {member_name or child.type} "
                    f"({child.type}, "
                    f"lines {start}-{end})"

                )

        return "\n".join(
            summary
        )

    def extract_name(
        self,
        node: Node,
    ):

        name_node = node.child_by_field_name(
            "name"
        )

        if name_node:

            return name_node.text.decode(
                "utf-8"
            )

        if node.type == "arrow_function":

            parent = node.parent

            if parent:

                if parent.type == "variable_declarator":

                    name_node = (
                        parent.child_by_field_name(
                            "name"
                        )
                    )

                    if name_node:

                        return name_node.text.decode(
                            "utf-8"
                        )

        return None
