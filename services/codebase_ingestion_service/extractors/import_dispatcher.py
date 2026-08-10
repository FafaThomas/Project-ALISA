from extractors.python_import_extractor import PythonImportExtractor
from extractors.csharp_import_extractor import CSharpImportExtractor
from extractors.javascript_import_extractor import JavaScriptImportExtractor
from extractors.html_import_extractor import HTMLImportExtractor
from extractors.css_import_extractor import CSSImportExtractor

class ImportDispatcher:

    def __init__(self):

        self.extractors = {

            "tree_sitter_python":
                PythonImportExtractor(),

            "tree_sitter_c_sharp":
                CSharpImportExtractor(),

            "tree_sitter_javascript":
                JavaScriptImportExtractor(),

            "tree_sitter_html":
                HTMLImportExtractor(),

            "tree_sitter_css":
                CSSImportExtractor(),

        }

    def get(
        self,
        parser_name: str,
    ):

        return self.extractors.get(
            parser_name
        )