from tree_sitter import Language
import tree_sitter_c_sharp

from parsers.tree_sitter_parser import TreeSitterParser
from models.parse_result import ParseResult


class CSharpParser(TreeSitterParser):

    def __init__(self):

        super().__init__(
            Language(
                tree_sitter_c_sharp.language()
            )
        )

    def parse(self, source):

        tree = self.parse_tree(source)

        return ParseResult(
            source=source,
            tree=tree,
        )