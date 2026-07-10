from extractors.generic_chunk_extractor import GenericChunkExtractor
from extractors.python_chunk_extractor import PythonChunkExtractor
from extractors.sql_chunk_extractor import SQLChunkExtractor


class ChunkDispatcher:

    def __init__(self):

        self.extractors = {

            "tree_sitter_python": PythonChunkExtractor(),

            "tree_sitter_sql": SQLChunkExtractor(),

            "generic": GenericChunkExtractor(),

        }

    def get(self, parser_name):

        return self.extractors.get(
            parser_name,
            self.extractors["generic"],
        )