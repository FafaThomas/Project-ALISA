from models.parsed_document import ParsedDocument


class ParsedDocumentBuilder:

    def build(self, parse_result):

        document = parse_result.document

        return ParsedDocument(

            relative_path=document.relative_path,

            language="sql",

            parser=document.parser,

            interpreter=document.dialect,

            symbols=[],

            imports=[],

            chunks=[],

            metadata={},

            calls=[],

        )