from tree_sitter import Language
import tree_sitter_sql

from parsers.tree_sitter_parser import TreeSitterParser
from models.sql_parse_result import SQLParseResult


class TreeSitterSQLParser(TreeSitterParser):

    def __init__(self):

        super().__init__(

            Language(tree_sitter_sql.language())

        )

    def parse(self, document):

        tree = self.parse_tree(document)

        return SQLParseResult(

            document=document,

            tree=tree,

        )