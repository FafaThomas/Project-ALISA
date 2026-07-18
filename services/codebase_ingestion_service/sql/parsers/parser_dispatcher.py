from sql.parsers.tree_sitter_sql_parser import (
    TreeSitterSQLParser
)

from sql.parsers.mysql_parser import MySQLParser
from sql.parsers.postgres_parser import PostgreSQLParser
from sql.parsers.oracle_parser import OracleParser
from sql.parsers.sqlite_parser import SQLiteParser
from sql.parsers.tsql_parser import TSQLParser


class SQLParserDispatcher:

    def __init__(self):

        self.parsers = {

            "tree_sitter_sql": TreeSitterSQLParser(),

            "tsql": TSQLParser(),

            "postgres": PostgreSQLParser(),

            "mysql": MySQLParser(),

            "sqlite": SQLiteParser(),

            "oracle": OracleParser(),

        }

    def get_parser(self, parser_name):

        return self.parsers[parser_name]