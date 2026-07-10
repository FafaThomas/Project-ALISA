from models.chunk import Chunk

from extractors.base_chunk_extractor import BaseChunkExtractor


class GenericChunkExtractor(BaseChunkExtractor):

    def extract(self, parse_result):

        source = parse_result.source.raw_source

        return [
            Chunk(
                id="full_document",
                type="document",
                name=str(parse_result.source.relative_path),
                start_line=1,
                end_line=len(source.splitlines()),
                content=source,
            )
        ]