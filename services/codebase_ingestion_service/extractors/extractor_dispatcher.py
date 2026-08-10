from extractors.python_symbol_extractor import PythonSymbolExtractor
from extractors.csharp_symbol_extractor import CSharpSymbolExtractor
from extractors.javascript_symbol_extractor import JavaScriptSymbolExtractor

class ExtractorDispatcher:

    def __init__(self):

        self.extractors = {

            "tree_sitter_python": PythonSymbolExtractor(),

            "tree_sitter_c_sharp": CSharpSymbolExtractor(),

            "tree_sitter_javascript": JavaScriptSymbolExtractor(),

        }

    def get(self, parser_name: str):

        return self.extractors.get(parser_name)