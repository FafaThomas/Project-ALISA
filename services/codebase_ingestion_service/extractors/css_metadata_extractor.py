from extractors.generic_metadata_extractor import (
    GenericMetadataExtractor,
)

from models.metadata import Metadata
from models.parse_result import ParseResult


class CSSMetadataExtractor(
    GenericMetadataExtractor
):

    def extract(
        self,
        parse_result: ParseResult,
    ) -> Metadata:

        metadata = super().extract(
            parse_result
        )

        source = parse_result.source.raw_source

        lines = source.splitlines()

        metadata.comment_lines = sum(

            1

            for line in lines

            if "/*" in line
            or line.strip().startswith("*")
            or "*/" in line

        )

        metadata.has_classes = (
            "." in source
        )

        metadata.has_functions = (
            "url(" in source
        )

        metadata.has_main = False

        metadata.has_tests = False

        return metadata