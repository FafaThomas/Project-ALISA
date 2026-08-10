from extractors.python_call_extractor import PythonCallExtractor
from extractors.csharp_call_extractor import CSharpCallExtractor
from extractors.base_call_extractor import BaseCallExtractor


class CallDispatcher:

    def __init__(self):

        self.extractors = {

            "tree_sitter_python":
                PythonCallExtractor(),

            "tree_sitter_c_sharp":
                CSharpCallExtractor(),

        }

    def get(
        self,
        parser: str
    ) -> BaseCallExtractor:

        return self.extractors.get(parser)