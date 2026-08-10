from extractors.generic_metadata_extractor import (
    GenericMetadataExtractor
)

from models.metadata import Metadata
from models.parse_result import ParseResult


class JavaScriptMetadataExtractor(
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

            if line.strip().startswith(
                "//"
            )

            or line.strip().startswith(
                "/*"
            )

            or line.strip().startswith(
                "*"
            )

        )

        metadata.has_main = (
            "main(" in source
        )

        metadata.has_classes = (
            "class " in source
        )

        metadata.has_functions = any(

            marker in source

            for marker in (
                "function ",
                "=>",
            )

        )

        return metadata