from models.metadata import Metadata
from models.parse_result import ParseResult


class GenericMetadataExtractor:

    def extract(self, parse_result: ParseResult) -> Metadata:

        source = parse_result.source.raw_source

        lines = source.splitlines()

        blank = sum(1 for line in lines if not line.strip())

        return Metadata(
            lines=len(lines),
            blank_lines=blank,
        )