from tree_sitter import Node

from models.import_symbol import ImportSymbol


class CSharpImportExtractor:

    def extract(self, tree):

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

        if node.type == "using_directive":

            self.extract_using_directive(
                node,
                imports,
            )

        for child in node.children:

            self.walk(
                child,
                imports,
            )

    def extract_using_directive(
        self,
        node: Node,
        imports: list,
    ):

        text = node.text.decode("utf-8").strip()

        # Remove "using"
        text = text[len("using"):].strip()

        # Remove trailing semicolon
        text = text.rstrip(";").strip()

        alias = None

        # using Foo = Bar;
        if "=" in text:

            alias, module = text.split(
                "=",
                1,
            )

            alias = alias.strip()
            module = module.strip()

        else:

            module = text

        imports.append(
            ImportSymbol(
                module=module,
                alias=alias,
                import_type="using",
            )
        )