from sql.parsers.tree_sitter_sql_parser import (
    TreeSitterSQLParser
)


class SQLParserDispatcher:

    def __init__(self):

        self.parsers = {

            "tree_sitter_sql": TreeSitterSQLParser(),

        }

    def register(self, name, parser):

        self.parsers[name] = parser

    def get_parser(self, name):

        parser = self.parsers.get(name)

        if parser is None:

            raise ValueError(

                f"Unknown SQL parser: {name}"

            )

        return parser