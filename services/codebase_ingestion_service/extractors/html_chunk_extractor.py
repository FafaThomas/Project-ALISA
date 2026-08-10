from tree_sitter import Node

from models.chunk import Chunk

from extractors.tree_sitter_chunk_extractor import (
    TreeSitterChunkExtractor,
)


class HTMLChunkExtractor(
    TreeSitterChunkExtractor
):

    def is_chunk(
        self,
        node: Node,
    ):

        return node.type in {
            "element",
            "script_element",
            "style_element",
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

        name_node = node.child_by_field_name(
            "tag_name"
        )

        name = (
            name_node.text.decode(
                "utf-8"
            )
            if name_node
            else node.type
        )

        return Chunk(

            id=f"{start}:{end}",

            type=node.type,

            name=name,

            start_line=start,

            end_line=end,

            content=text,

        )