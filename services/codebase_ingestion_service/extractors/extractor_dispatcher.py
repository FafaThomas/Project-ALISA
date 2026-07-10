from extractors.python_symbol_extractor import PythonSymbolExtractor


class ExtractorDispatcher:

    def __init__(self):

        self.extractors = {
            "tree_sitter_python": PythonSymbolExtractor(),
        }

    def get(self, parser_name: str):

        return self.extractors.get(parser_name)