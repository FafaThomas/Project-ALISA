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
        "a": "href",
    }

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

        if node.type in {
            "script_element",
            "style_element",
            "element",
        }:

            self.extract_resource(
                node,
                imports,
            )

        for child in node.children:

            self.walk(
                child,
                imports,
            )

    def extract_resource(
        self,
        node: Node,
        imports: list,
    ):

        tag_name = None

        if node.type == "script_element":

            tag_name = "script"

        elif node.type == "style_element":

            return

        else:

            tag_node = node.child_by_field_name(
                "tag_name"
            )

            if not tag_node:
                return

            tag_name = tag_node.text.decode(
                "utf-8"
            )

        attribute_name = self.RESOURCE_ATTRIBUTES.get(
            tag_name
        )

        if not attribute_name:
            return

        attributes = node.child_by_field_name(
            "attributes"
        )

        if not attributes:
            return

        for attribute in attributes.children:

            if attribute.type != "attribute":
                continue

            name_node = attribute.child_by_field_name(
                "name"
            )

            value_node = attribute.child_by_field_name(
                "value"
            )

            if not name_node or not value_node:
                continue

            name = name_node.text.decode(
                "utf-8"
            )

            if name != attribute_name:
                continue

            value = value_node.text.decode(
                "utf-8"
            ).strip("\"'")

            imports.append(
                ImportSymbol(
                    module=value,
                    alias=None,
                    import_type=tag_name,
                )
            )