from extractors.python_call_extractor import PythonCallExtractor
from extractors.base_call_extractor import BaseCallExtractor


class CallDispatcher:

    def __init__(self):

        self.extractors = {
            "tree_sitter_python": PythonCallExtractor()
        }

    def get(
        self,
        parser: str
    ) -> BaseCallExtractor:

        return self.extractors.get(parser)