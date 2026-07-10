from parsers.base_parser import BaseParser

from models.parse_result import ParseResult


class GenericParser(BaseParser):

    def parse(self, source):

        return ParseResult(

            source=source,

            tree=None,

        )