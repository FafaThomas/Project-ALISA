from tree_sitter import Node

from models.import_symbol import ImportSymbol


class JavaScriptImportExtractor:

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

            self.extract_require(
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

        # Remove "import"
        text = text[len("import"):].strip()

        # Remove trailing semicolon
        text = text.rstrip(";").strip()

        # import "module"
        if text.startswith("\"") or text.startswith("'"):

            module = text.strip(
                "\"'"
            )

            imports.append(
                ImportSymbol(
                    module=module,
                    alias=None,
                    import_type="import",
                )
            )

            return

        # import ... from "module"
        if " from " in text:

            left, right = text.rsplit(
                " from ",
                1,
            )

            module = right.strip(
                "\"'"
            )

            imports.append(
                ImportSymbol(
                    module=module,
                    alias=left.strip(),
                    import_type="import",
                )
            )

    def extract_require(
        self,
        node: Node,
        imports: list,
    ):

        text = node.text.decode(
            "utf-8"
        ).strip()

        if not text.startswith(
            "require("
        ):

            return

        if "\"" in text:

            module = text.split(
                "\"",
                2,
            )[1]

        elif "'" in text:

            module = text.split(
                "'",
                2,
            )[1]

        else:

            return

        imports.append(
            ImportSymbol(
                module=module,
                alias=None,
                import_type="require",
            )
        )