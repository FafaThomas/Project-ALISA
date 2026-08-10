from tree_sitter import Node

from models.import_symbol import ImportSymbol


class CSSImportExtractor:

    def extract(
        self,
        tree,
    ):

        imports = []

        self.walk(
            tree.root_node,
            imports,
        )

        return imports

    def walk(
        self,
        node: Node,
        imports: list,
    ):

        if node.type == "import_statement":

            self.extract_import(
                node,
                imports,
            )

        elif node.type == "call_expression":

            self.extract_url(
                node,
                imports,
            )

        for child in node.children:

            self.walk(
                child,
                imports,
            )

    def extract_import(
        self,
        node: Node,
        imports: list,
    ):

        text = node.text.decode(
            "utf-8"
        ).strip()

        # Remove @import
        text = text[len("@import"):].strip()

        # Remove semicolon
        text = text.rstrip(";").strip()

        if text.startswith(
            "\""
        ) or text.startswith(
            "'"
        ):

            module = text.strip(
                "\"'"
            )

        elif text.startswith(
            "url("
        ):

            module = (
                text[4:]
                .rstrip(")")
                .strip()
                .strip("\"'")
            )

        else:

            return

        imports.append(
            ImportSymbol(
                module=module,
                alias=None,
                import_type="css_import",
            )
        )

    def extract_url(
        self,
        node: Node,
        imports: list,
    ):

        function_name = node.child_by_field_name(
            "name"
        )

        arguments = node.child_by_field_name(
            "arguments"
        )

        if not function_name or not arguments:
            return

        name = function_name.text.decode(
            "utf-8"
        )

        if name != "url":
            return

        text = self.extract_argument_text(
            arguments
        )

        if not text:
            return

        imports.append(
            ImportSymbol(
                module=text,
                alias=None,
                import_type="css_url",
            )
        )

    def extract_argument_text(
        self,
        node: Node,
    ):

        text = node.text.decode(
            "utf-8"
        ).strip()

        return text.strip(
            "\"'"
        )