from parsers.python_parser import PythonParser
from parsers.csharp_parser import CSharpParser
from parsers.generic_parser import GenericParser


class ParserDispatcher:

    def __init__(self):

        self.parsers = {

            "tree_sitter_python": PythonParser(),

            "tree_sitter_c_sharp": CSharpParser(),

            "generic": GenericParser(),

        }

    def get_parser(self, parser):

        return self.parsers.get(
            parser,
            self.parsers["generic"]
        )