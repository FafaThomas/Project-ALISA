from tree_sitter import Language
import tree_sitter_c_sharp

from parsers.tree_sitter_parser import TreeSitterParser


class CSharpParser(TreeSitterParser):

    def __init__(self):

        super().__init__(
            Language(
                tree_sitter_c_sharp.language()
            )
        )