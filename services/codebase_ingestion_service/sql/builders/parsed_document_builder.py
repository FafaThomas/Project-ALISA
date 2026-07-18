from models.parsed_document import ParsedDocument


class ParsedDocumentBuilder:

    def build(
        self,
        parse_result,
        symbols,
        chunks,
        metadata,
    ):

        document = parse_result.document

        return ParsedDocument(

            relative_path=document.relative_path,

            language="sql",

            parser=document.parser,

            interpreter=document.dialect,

            symbols=symbols,

            imports=[],

            chunks=chunks,

            metadata=metadata,

            calls=[],
        )