from tree_sitter import Language, Parser

import tree_sitter_javascript
import tree_sitter_css


class EmbeddedLanguageParser:

    def __init__(self):

        self.parsers = {
            "javascript": Parser(
                Language(
                    tree_sitter_javascript.language()
                )
            ),

            "css": Parser(
                Language(
                    tree_sitter_css.language()
                )
            ),
        }

    def parse(
        self,
        language: str,
        source: str,
    ):

        parser = self.parsers.get(
            language
        )

        if not parser:
            return None

        return parser.parse(
            source.encode("utf-8")
        )