from models.parse_result import ParseResult

class GenericDocumentParser:

    def parse(self, source):

        return ParseResult(
            ast=None,
            source=source,
        )