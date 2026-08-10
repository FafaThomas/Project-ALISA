from extractors.python_import_extractor import PythonImportExtractor
from extractors.csharp_import_extractor import CSharpImportExtractor


class ImportDispatcher:

    def __init__(self):

        self.extractors = {

            "tree_sitter_python":
                PythonImportExtractor(),

            "tree_sitter_c_sharp":
                CSharpImportExtractor(),

        }

    def get(
        self,
        parser_name: str,
    ):

        return self.extractors.get(
            parser_name
        )