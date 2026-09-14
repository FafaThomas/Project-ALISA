from tree_sitter import Node

from models.chunk import Chunk


class SQLChunkExtractor:

    CHUNK_NODES = {
        # Generic / ANSI SQL
        "create_table",
        "create_view",
        "create_function",
        "create_procedure",

        # PostgreSQL
        "create_function_statement",
        "create_procedure_statement",
        "create_view_statement",
        "create_table_statement",

        # MySQL
        "create_function_statement",
        "create_procedure_statement",
        "create_view_statement",
        "create_table_statement",

        # Microsoft SQL Server
        "create_function",
        "create_procedure",
        "create_view",
        "create_table",
    }

    def extract(
        self,
        parse_result,
    ):

        source = parse_result.document.raw_source

        lines = source.splitlines()

        chunks = []

        self.walk(
            parse_result.tree.root_node,
            lines,
            chunks,
        )

        return chunks

    def walk(
        self,
        node: Node,
        lines,
        chunks,
    ):

        if self.is_chunk(node):

            chunk = self.create_chunk(
                node,
                lines,
            )

            if chunk:

                chunks.append(chunk)

                # Do not create nested chunks
                # from the same declaration.
                return

        for child in node.children:

            self.walk(
                child,
                lines,
                chunks,
            )

    def is_chunk(
        self,
        node: Node,
    ):

        return node.type in self.CHUNK_NODES

    def create_chunk(
        self,
        node: Node,
        lines,
    ):

        start = node.start_point[0] + 1

        end = node.end_point[0] + 1

        text = "\n".join(
            lines[start - 1:end]
        )

        if not text.strip():

            return None

        return Chunk(

            id=f"{start}:{end}",

            type=self.chunk_type(
                node
            ),

            name=self.extract_name(
                node
            ),

            start_line=start,

            end_line=end,

            content=text,

        )

    def chunk_type(
        self,
        node: Node,
    ):

        node_type = node.type.lower()

        if "table" in node_type:

            return "table"

        if "view" in node_type:

            return "view"

        if "function" in node_type:

            return "function"

        if "procedure" in node_type:

            return "procedure"

        return node.type

    def extract_name(
        self,
        node: Node,
    ):

        name_node = node.child_by_field_name(
            "name"
        )

        if name_node:

            return name_node.text.decode(
                "utf-8"
            )

        # Some SQL grammars don't expose
        # the declaration name through a
        # "name" field. Fall back to finding
        # the identifier in the declaration.

        return self.find_identifier(
            node
        )

    def find_identifier(
        self,
        node: Node,
    ):

        for child in node.children:

            if child.type in {
                "identifier",
                "object_name",
                "qualified_name",
            }:

                return child.text.decode(
                    "utf-8"
                )

            result = self.find_identifier(
                child
            )

            if result:

                return result

        return None
