from extractors.python_symbol_extractor import PythonSymbolExtractor
from extractors.csharp_symbol_extractor import CSharpSymbolExtractor


class ExtractorDispatcher:

    def __init__(self):

        self.extractors = {

            "tree_sitter_python": PythonSymbolExtractor(),

            "tree_sitter_c_sharp": CSharpSymbolExtractor(),

        }

    def get(self, parser_name: str):

        return self.extractors.get(parser_name)