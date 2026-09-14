from models.parse_result import ParseResult

from extractors.embedded_language_parser import (
    EmbeddedLanguageParser,
)

from extractors.extractor_dispatcher import (
    ExtractorDispatcher,
)

from extractors.import_dispatcher import (
    ImportDispatcher,
)

from extractors.chunk_dispatcher import (
    ChunkDispatcher,
)

from extractors.call_dispatcher import (
    CallDispatcher,
)

from extractors.embedded_source import (
    EmbeddedSource,
)


class HTMLEmbeddedExtractor:

    def __init__(self):

        self.embedded_parser = (
            EmbeddedLanguageParser()
        )

        self.extractor_dispatcher = (
            ExtractorDispatcher()
        )

        self.import_dispatcher = (
            ImportDispatcher()
        )

        self.chunk_dispatcher = (
            ChunkDispatcher()
        )

        self.call_dispatcher = (
            CallDispatcher()
        )

    def extract(
        self,
        host_source,
        html_tree,
    ):

        results = {

            "symbols": [],

            "imports": [],

            "chunks": [],

            "calls": [],

        }

        self.walk(
            host_source,
            html_tree.root_node,
            results,
        )

        return results

    def walk(
        self,
        host_source,
        node,
        results,
    ):

        if node.type == "script_element":

            self.extract_embedded(
                host_source,
                node,
                "javascript",
                "tree_sitter_javascript",
                results,
            )

        elif node.type == "style_element":

            self.extract_embedded(
                host_source,
                node,
                "css",
                "tree_sitter_css",
                results,
            )

        for child in node.children:

            self.walk(
                host_source,
                child,
                results,
            )

    def extract_embedded(
        self,
        host_source,
        node,
        language,
        parser_name,
        results,
    ):

        source_node = self.get_content_node(
            node
        )

        if not source_node:

            return

        embedded_source_text = (
            source_node.text.decode(
                "utf-8"
            )
        )

        if not embedded_source_text.strip():

            return

        tree = self.embedded_parser.parse(
            language,
            embedded_source_text,
        )

        if tree is None:

            return

        embedded_source = EmbeddedSource(
            host_source,
            embedded_source_text,
        )

        parse_result = ParseResult(
            embedded_source,
            tree,
        )

        # -------------------------
        # Symbols
        # -------------------------

        symbol_extractor = (
            self.extractor_dispatcher.get(
                parser_name
            )
        )

        if symbol_extractor:

            symbols = symbol_extractor.extract(
                tree
            )

            self.offset_symbols(
                symbols,
                source_node.start_point[0],
            )

            results["symbols"].extend(
                symbols
            )

        # -------------------------
        # Imports
        # -------------------------

        import_extractor = (
            self.import_dispatcher.get(
                parser_name
            )
        )

        if import_extractor:

            imports = import_extractor.extract(
                tree
            )

            results["imports"].extend(
                imports
            )

        # -------------------------
        # Chunks
        # -------------------------

        chunk_extractor = (
            self.chunk_dispatcher.get(
                parser_name
            )
        )

        if chunk_extractor:

            chunks = chunk_extractor.extract(
                parse_result
            )

            self.offset_chunks(
                chunks,
                source_node.start_point[0],
            )

            results["chunks"].extend(
                chunks
            )

        # -------------------------
        # Calls
        # -------------------------

        call_extractor = (
            self.call_dispatcher.get(
                parser_name
            )
        )

        if call_extractor:

            calls = call_extractor.extract(
                parse_result
            )

            self.offset_calls(
                calls,
                source_node.start_point[0],
            )

            results["calls"].extend(
                calls
            )

    def get_content_node(
        self,
        node,
    ):

        for child in node.children:

            if child.type in {
                "raw_text",
                "script",
                "style",
            }:

                return child

        return None

    def offset_symbols(
        self,
        symbols,
        line_offset,
    ):

        for symbol in symbols:

            symbol.start_line += (
                line_offset
            )

            symbol.end_line += (
                line_offset
            )

    def offset_chunks(
        self,
        chunks,
        line_offset,
    ):

        for chunk in chunks:

            chunk.start_line += (
                line_offset
            )

            chunk.end_line += (
                line_offset
            )

    def offset_calls(
        self,
        calls,
        line_offset,
    ):

        for call in calls:

            call.line += (
                line_offset
            )