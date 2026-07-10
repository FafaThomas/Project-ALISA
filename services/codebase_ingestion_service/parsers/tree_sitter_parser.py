from tree_sitter import Parser

from models.parse_result import ParseResult


class TreeSitterParser:

    def __init__(self, language):

        self.parser = Parser()
        self.parser.language = language

    def parse(self, source):

        tree = self.parser.parse(
            bytes(source.raw_source, "utf-8")
        )

        return ParseResult(
            source=source,
            tree=tree
        )