from tree_sitter import Node

from models.symbol import Symbol

from extractors.base_extractor import BaseExtractor


class CSharpSymbolExtractor(BaseExtractor):

    def extract(self, tree):

        symbols = []

        self.walk(
            tree.root_node,
            symbols,
            parent=None,
        )

        return symbols

    def walk(
        self,
        node: Node,
        symbols,
        parent=None,
    ):

        symbol = None

        # -------------------------
        # Namespace
        # -------------------------

        if node.type == "namespace_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="namespace",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Class
        # -------------------------

        elif node.type == "class_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="class",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Record
        # -------------------------

        elif node.type == "record_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="record",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Struct
        # -------------------------

        elif node.type == "struct_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="struct",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Interface
        # -------------------------

        elif node.type == "interface_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="interface",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Enum
        # -------------------------

        elif node.type == "enum_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="enum",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Constructor
        # -------------------------

        elif node.type == "constructor_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="constructor",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Method
        # -------------------------

        elif node.type == "method_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="method",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Property
        # -------------------------

        elif node.type == "property_declaration":

            name_node = node.child_by_field_name("name")

            if name_node:

                symbol = Symbol(

                    name=name_node.text.decode(),

                    qualified_name="",

                    kind="property",

                    start_line=node.start_point[0] + 1,

                    end_line=node.end_point[0] + 1,

                    parent=parent,

                )

        # -------------------------
        # Field
        # -------------------------

        elif node.type == "field_declaration":

            variable = node.child_by_field_name("declarator")

            if variable:

                name_node = variable.child_by_field_name("name")

                if name_node:

                    symbol = Symbol(

                        name=name_node.text.decode(),

                        qualified_name="",

                        kind="field",

                        start_line=node.start_point[0] + 1,

                        end_line=node.end_point[0] + 1,

                        parent=parent,

                    )

        if symbol:

            symbols.append(symbol)

            parent = symbol.name

        for child in node.children:

            self.walk(
                child,
                symbols,
                parent,
            )