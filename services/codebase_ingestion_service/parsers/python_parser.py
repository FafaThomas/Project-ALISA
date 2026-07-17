from tree_sitter import Language
import tree_sitter_python

from parsers.tree_sitter_parser import TreeSitterParser
from models.parse_result import ParseResult


class PythonParser(TreeSitterParser):

    def __init__(self):

        super().__init__(

            Language(tree_sitter_python.language())

        )

    def parse(self, source):

        tree = self.parse_tree(source)

        return ParseResult(

            source=source,

            tree=tree,

        )