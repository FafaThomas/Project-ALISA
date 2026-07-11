from models.chunk import Chunk


class ManifestChunkExtractor:

    def extract(self, parse_result):

        text = parse_result.source.content

        if not text.strip():
            return []

        return [

            Chunk(

                id="0",

                type="document",

                name=parse_result.source.relative_path,

                start_line=1,

                end_line=len(text.splitlines()),

                content=text,
            )

        ]