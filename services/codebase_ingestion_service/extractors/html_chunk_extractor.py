from tree_sitter import Node

from models.chunk import Chunk

from extractors.tree_sitter_chunk_extractor import (
    TreeSitterChunkExtractor,
)


class HTMLChunkExtractor(
    TreeSitterChunkExtractor
):

    HTML_CHUNK_NODES = {
        "script_element",
        "style_element",
        "form_element",
        "nav_element",
        "header_element",
        "footer_element",
        "main_element",
        "section_element",
        "article_element",
    }

    def is_chunk(
        self,
        node: Node,
    ):

        return node.type in self.HTML_CHUNK_NODES

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

    def extract_name(
        self,
        node: Node,
    ):

        if node.type in {
            "script_element",
            "style_element",
        }:

            return node.type

        return node.type
