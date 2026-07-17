from abc import ABC, abstractmethod

from tree_sitter import Node

from extractors.base_chunk_extractor import BaseChunkExtractor

from models.chunk import Chunk


class TreeSitterChunkExtractor(
    BaseChunkExtractor,
    ABC
):

    def extract(self, parse_result):

        source = parse_result.source.raw_source

        lines = source.splitlines()

        chunks = []

        self.walk(
            parse_result.tree.root_node,
            lines,
            chunks,
        )

        return chunks

    def walk(
        self,
        node: Node,
        lines,
        chunks,
    ):

        if self.is_chunk(node):

            chunk = self.create_chunk(
                node,
                lines,
            )

            if chunk:

                chunks.append(chunk)

        for child in node.children:

            self.walk(
                child,
                lines,
                chunks,
            )

    @abstractmethod
    def is_chunk(
        self,
        node: Node,
    ):

        pass

    @abstractmethod
    def create_chunk(
        self,
        node: Node,
        lines,
    ) -> Chunk | None:

        pass