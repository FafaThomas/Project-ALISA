from extractors.python_call_extractor import PythonCallExtractor
from extractors.csharp_call_extractor import CSharpCallExtractor
from extractors.base_call_extractor import BaseCallExtractor
from extractors.javascript_call_extractor import JavaScriptCallExtractor
from extractors.html_call_extractor import HTMLCallExtractor

class CallDispatcher:

    def __init__(self):

        self.extractors = {

            "tree_sitter_python":
                PythonCallExtractor(),

            "tree_sitter_c_sharp":
                CSharpCallExtractor(),

            "tree_sitter_javascript":
                JavaScriptCallExtractor(),

            "tree_sitter_html":
                HTMLCallExtractor(),

        }

    def get(
        self,
        parser: str
    ) -> BaseCallExtractor:

        return self.extractors.get(parser)