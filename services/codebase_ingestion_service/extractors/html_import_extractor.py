from tree_sitter import Node

from models.import_symbol import ImportSymbol


class HTMLImportExtractor:

    RESOURCE_ATTRIBUTES = {
        "script": "src",
        "link": "href",
        "img": "src",
        "iframe": "src",
        "video": "src",
        "audio": "src",
        "source": "src",
    }

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

        if node.type == "element":

            self.extract_element(
                node,
                imports,
            )

        elif node.type == "script_element":

            self.extract_script_element(
                node,
                imports,
            )

        for child in node.children:

            self.walk(
                child,
                imports,
            )

    def extract_element(
        self,
        node: Node,
        imports: list,
    ):

        tag_name = None

        start_tag = None

        for child in node.children:

            if child.type == "start_tag":

                start_tag = child
                break

        if not start_tag:
            return

        for child in start_tag.children:

            if child.type == "tag_name":

                tag_name = (
                    child.text
                    .decode("utf-8")
                    .lower()
                )

                break

        if not tag_name:
            return

        attribute_name = (
            self.RESOURCE_ATTRIBUTES.get(
                tag_name
            )
        )

        if not attribute_name:
            return

        for child in start_tag.children:

            if child.type != "attribute":
                continue

            name = None
            value = None

            for attribute_child in child.children:

                if attribute_child.type == "attribute_name":

                    name = (
                        attribute_child.text
                        .decode("utf-8")
                        .lower()
                    )

                elif attribute_child.type == "quoted_attribute_value":

                    value = (
                        attribute_child.text
                        .decode("utf-8")
                        .strip("\"'")
                    )

                elif attribute_child.type == "attribute_value":

                    value = (
                        attribute_child.text
                        .decode("utf-8")
                    )

            if name != attribute_name:
                continue

            if not value:
                continue

            imports.append(
                ImportSymbol(
                    module=value,
                    alias=None,
                    import_type=tag_name,
                )
            )

    def extract_script_element(
        self,
        node: Node,
        imports: list,
    ):

        start_tag = None

        for child in node.children:

            if child.type == "start_tag":

                start_tag = child
                break

        if not start_tag:
            return

        for child in start_tag.children:

            if child.type != "attribute":
                continue

            name = None
            value = None

            for attribute_child in child.children:

                if attribute_child.type == "attribute_name":

                    name = (
                        attribute_child.text
                        .decode("utf-8")
                        .lower()
                    )

                elif attribute_child.type == "quoted_attribute_value":

                    value = (
                        attribute_child.text
                        .decode("utf-8")
                        .strip("\"'")
                    )

                elif attribute_child.type == "attribute_value":

                    value = (
                        attribute_child.text
                        .decode("utf-8")
                    )

            if name != "src":
                continue

            if not value:
                continue

            imports.append(
                ImportSymbol(
                    module=value,
                    alias=None,
                    import_type="script",
                )
            )