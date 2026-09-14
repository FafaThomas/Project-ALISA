from tree_sitter import Node

from models.chunk import Chunk

from extractors.tree_sitter_chunk_extractor import (
    TreeSitterChunkExtractor,
)


class PythonChunkExtractor(
    TreeSitterChunkExtractor
):

    MAX_CLASS_CHARS = 12000

    def is_chunk(
        self,
        node: Node,
    ):

        return node.type in {

            "function_definition",

            "class_definition",

        }

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

        if (
            node.type == "class_definition"
            and len(text) > self.MAX_CLASS_CHARS
        ):

            text = self.create_class_summary(
                node,
            )

        return Chunk(

            id=f"{start}:{end}",

            type=node.type,

            name=self.extract_name(node),

            start_line=start,

            end_line=end,

            content=text,

        )

    def create_class_summary(
        self,
        node: Node,
    ):

        name = self.extract_name(
            node,
        )

        summary = [

            f"class {name}",

            "",

            "Methods:",

        ]

        for child in node.children:

            if child.type == "function_definition":

                method_name = self.extract_name(
                    child,
                )

                start = (
                    child.start_point[0] + 1
                )

                end = (
                    child.end_point[0] + 1
                )

                summary.append(

                    f"- {method_name} "
                    f"(lines {start}-{end})"

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

            return name_node.text.decode()

        return None

