from extractors.generic_metadata_extractor import (
    GenericMetadataExtractor,
)

from models.metadata import Metadata
from models.parse_result import ParseResult


class HTMLMetadataExtractor(
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

        metadata.comment_lines = sum(

            1

            for line in source.splitlines()

            if "<!--" in line
        )

        metadata.has_classes = (
            "class=" in source
        )

        metadata.has_functions = (
            "<script" in source
        )

        metadata.has_main = (
            "<main" in source
        )

        metadata.has_tests = False

        return metadata