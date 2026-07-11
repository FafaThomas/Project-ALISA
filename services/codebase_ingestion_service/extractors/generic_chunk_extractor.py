from models.chunk import Chunk


class GenericChunkExtractor:

    def extract(
        self,
        parse_result,
    ):

        text = parse_result.source.raw_source

        if not text.strip():
            return []

        return [

            Chunk(

                id="0",

                type="document",

                name=str(parse_result.source.relative_path),

                start_line=1,

                end_line=len(
                    text.splitlines()
                ),

                content=text,
            )

        ]