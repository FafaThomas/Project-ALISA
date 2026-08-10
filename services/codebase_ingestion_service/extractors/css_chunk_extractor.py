from tree_sitter import Node

from models.chunk import Chunk

from extractors.tree_sitter_chunk_extractor import (
    TreeSitterChunkExtractor,
)


class CSSChunkExtractor(
    TreeSitterChunkExtractor
):

    def is_chunk(
        self,
        node: Node,
    ):

        return node.type in {
            "rule_set",
            "media_statement",
            "keyframes_statement",
            "import_statement",
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

        name_node = None

        if node.type == "rule_set":

            name_node = node.child_by_field_name(
                "selector"
            )

        elif node.type == "keyframes_statement":

            name_node = node.child_by_field_name(
                "name"
            )

        name = (

            name_node.text.decode(
                "utf-8"
            ).strip()

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