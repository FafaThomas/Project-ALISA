from models.parse_result import ParseResult


class ManifestParser:

    def parse(self, manifest):

        return ParseResult(

            source=manifest,

            tree=None,
        )