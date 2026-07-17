from tree_sitter import Parser


class TreeSitterParser:

    def __init__(self, language):

        self.parser = Parser()
        self.parser.language = language

    def parse_tree(self, source):

        return self.parser.parse(

            bytes(source.raw_source, "utf-8")

        )