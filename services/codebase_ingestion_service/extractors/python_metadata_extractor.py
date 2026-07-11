from extractors.generic_metadata_extractor import GenericMetadataExtractor

from models.metadata import Metadata
from models.parse_result import ParseResult


class PythonMetadataExtractor(GenericMetadataExtractor):

    def extract(self, parse_result: ParseResult) -> Metadata:

        metadata = super().extract(parse_result)

        source = parse_result.source.raw_source

        lines = source.splitlines()

        metadata.comment_lines = sum(
            1
            for line in lines
            if line.strip().startswith("#")
        )

        metadata.has_main = "__name__" in source

        metadata.has_classes = "class " in source

        metadata.has_functions = "def " in source

        return metadata