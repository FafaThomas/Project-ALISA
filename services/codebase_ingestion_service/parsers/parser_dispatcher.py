from parsers.python_parser import PythonParser
from parsers.generic_parser import GenericParser
from parsers.sql_parser import SQLParser


class ParserDispatcher:

    def __init__(self):

        self.parsers = {

            "tree_sitter_python": PythonParser(),

            "tree_sitter_sql": SQLParser(),

            "generic": GenericParser(),

        }

    def get_parser(self, parser_name):

        return self.parsers.get(
            parser_name,
            self.parsers["generic"],
        )