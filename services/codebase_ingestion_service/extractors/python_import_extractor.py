from tree_sitter import Node

from models.import_symbol import ImportSymbol


class PythonImportExtractor:

    def extract(self, tree):

        imports = []

        self.walk(tree.root_node, imports)

        return imports

    def walk(self, node: Node, imports: list):

        if node.type == "import_statement":

            self.extract_import_statement(node, imports)

        elif node.type == "import_from_statement":

            self.extract_from_statement(node, imports)

        for child in node.children:

            self.walk(child, imports)

    def extract_import_statement(self, node: Node, imports: list):

        text = node.text.decode("utf-8")

        text = text.replace("import", "").strip()

        for item in text.split(","):

            item = item.strip()

            if " as " in item:

                module, alias = item.split(" as ")

            else:

                module = item

                alias = None

            imports.append(

                ImportSymbol(

                    module=module,

                    alias=alias,

                    import_type="import"

                )

            )

    def extract_from_statement(self, node: Node, imports: list):

        text = node.text.decode("utf-8")

        #
        # from pathlib import Path
        #

        left, right = text.split(" import ")

        module = left.replace("from", "").strip()

        for item in right.split(","):

            item = item.strip()

            if " as " in item:

                symbol, alias = item.split(" as ")

            else:

                symbol = item

                alias = None

            imports.append(

                ImportSymbol(

                    module=f"{module}.{symbol}",

                    alias=alias,

                    import_type="from"

                )

            )