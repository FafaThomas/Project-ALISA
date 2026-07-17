from tree_sitter import Node

from models.chunk import Chunk

from extractors.tree_sitter_chunk_extractor import (
    TreeSitterChunkExtractor,
)


class PythonChunkExtractor(
    TreeSitterChunkExtractor
):

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

        return Chunk(

            id=f"{start}:{end}",

            type=node.type,

            name=self.extract_name(node),

            start_line=start,

            end_line=end,

            content=text,

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