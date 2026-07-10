from models.chunk import Chunk

from extractors.base_chunk_extractor import BaseChunkExtractor


class PythonChunkExtractor(BaseChunkExtractor):

    def extract(self, parse_result):

        source = parse_result.source.raw_source

        lines = source.splitlines()

        chunks = []

        root = parse_result.tree.root_node

        self.walk(root, lines, chunks)

        return chunks

    def walk(self, node, lines, chunks):

        if node.type in {

            "function_definition",

            "class_definition",

        }:

            start = node.start_point[0] + 1

            end = node.end_point[0] + 1

            text = "\n".join(lines[start - 1:end])

            name = self.extract_name(node)

            chunks.append(

                Chunk(
                    id=f"{start}:{end}",
                    type=node.type,
                    name=name,
                    start_line=start,
                    end_line=end,
                    content=text,
                )

            )

        for child in node.children:

            self.walk(child, lines, chunks)

    def extract_name(self, node):

        for child in node.children:

            if child.type == "identifier":

                return child.text.decode()

        return None