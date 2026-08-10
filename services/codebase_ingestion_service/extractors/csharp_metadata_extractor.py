from extractors.generic_metadata_extractor import (
    GenericMetadataExtractor,
)

from models.metadata import Metadata
from models.parse_result import ParseResult


class CSharpMetadataExtractor(
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

        metadata.has_main = any(

            marker in source

            for marker in (
                "static void Main(",
                "static void Main (",
                "static async Task Main(",
                "static async Task Main (",
            )

        )

        metadata.has_classes = any(

            marker in source

            for marker in (
                "class ",
                "struct ",
                "interface ",
                "record ",
            )

        )

        metadata.has_functions = any(

            marker in source

            for marker in (
                "void ",
                "Task ",
                "async ",
            )

        )

        metadata.has_tests = any(

            marker in source

            for marker in (
                "[Fact]",
                "[Theory]",
                "[Test]",
                "[TestCase]",
                "[TestMethod]",
            )

        )

        return metadata