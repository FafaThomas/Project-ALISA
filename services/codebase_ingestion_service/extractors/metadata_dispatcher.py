from extractors.generic_metadata_extractor import GenericMetadataExtractor
from extractors.python_metadata_extractor import PythonMetadataExtractor
from extractors.csharp_metadata_extractor import CSharpMetadataExtractor
from extractors.javascript_metadata_extractor import JavaScriptMetadataExtractor
from extractors.html_metadata_extractor import HTMLMetadataExtractor
from extractors.css_metadata_extractor import CSSMetadataExtractor

class MetadataDispatcher:

    def __init__(self):

        self.extractors = {
            "tree_sitter_python": PythonMetadataExtractor(),
            "tree_sitter_javascript": JavaScriptMetadataExtractor(),
            "tree_sitter_c_sharp": CSharpMetadataExtractor(),
            "tree_sitter_html": HTMLMetadataExtractor(),
            "tree_sitter_css": CSSMetadataExtractor(),
            "generic": GenericMetadataExtractor(),
        }

    def get(self, parser_name: str):

        return self.extractors.get(
            parser_name,
            GenericMetadataExtractor(),
        )