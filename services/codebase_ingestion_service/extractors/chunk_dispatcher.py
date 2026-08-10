from extractors.generic_chunk_extractor import GenericChunkExtractor
from extractors.python_chunk_extractor import PythonChunkExtractor
from extractors.postgresql_chunk_extractor import PostgreSQLChunkExtractor
from extractors.csharp_chunk_extractor import CSharpChunkExtractor
from extractors.javascript_chunk_extractor import JavaScriptChunkExtractor
from extractors.html_chunk_extractor import HTMLChunkExtractor
from extractors.css_chunk_extractor import CSSChunkExtractor

class ChunkDispatcher:

    def __init__(self):

        self.extractors = {

            "tree_sitter_python": PythonChunkExtractor(),

            "tree_sitter_postgresql": PostgreSQLChunkExtractor(),

            "tree_sitter_c_sharp": CSharpChunkExtractor(),

            "tree_sitter_javascript": JavaScriptChunkExtractor(),

            "tree_sitter_html": HTMLChunkExtractor(),

            "tree_sitter_css": CSSChunkExtractor(),

            "generic": GenericChunkExtractor(),

        }

    def get(self, parser_name):

        return self.extractors.get(
            parser_name,
            self.extractors["generic"],
        )