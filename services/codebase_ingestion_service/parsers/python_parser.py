from tree_sitter import Language
import tree_sitter_python

from parsers.tree_sitter_parser import TreeSitterParser


class PythonParser(TreeSitterParser):

    def __init__(self):

        super().__init__(

            Language(tree_sitter_python.language())

        )