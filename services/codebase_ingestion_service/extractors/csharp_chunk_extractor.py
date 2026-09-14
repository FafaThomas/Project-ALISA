from tree_sitter import Node

from models.chunk import Chunk

from extractors.tree_sitter_chunk_extractor import (
    TreeSitterChunkExtractor,
)


class CSharpChunkExtractor(
    TreeSitterChunkExtractor
):

    MAX_TYPE_CHARS = 6000

    TYPE_NODES = {
        "class_declaration",
        "struct_declaration",
        "interface_declaration",
        "enum_declaration",
    }

    CHUNK_NODES = {
        "class_declaration",
        "struct_declaration",
        "interface_declaration",
        "enum_declaration",
        "method_declaration",
        "constructor_declaration",
        "property_declaration",
    }

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

        if (
            node.type in self.TYPE_NODES
            and len(text) > self.MAX_TYPE_CHARS
        ):

            text = self.create_type_summary(
                node,
            )

        name_node = node.child_by_field_name(
            "name"
        )

        name = (

            name_node.text.decode(
                "utf-8"
            )

            if name_node

            else None

        )

        return Chunk(

            id=f"{start}:{end}",

            type=node.type,

            name=name,

            start_line=start,

            end_line=end,

            content=text,

        )

    def create_type_summary(
        self,
        node: Node,
    ):

        name_node = node.child_by_field_name(
            "name"
        )

        name = (

            name_node.text.decode(
                "utf-8"
            )

            if name_node

            else None

        )

        summary = [

            f"{node.type}: {name}",

            "",

            "Members:",

        ]

        for child in node.children:

            if child.type in {
                "method_declaration",
                "constructor_declaration",
                "property_declaration",
                "class_declaration",
                "struct_declaration",
                "interface_declaration",
                "enum_declaration",
            }:

                member_name_node = (
                    child.child_by_field_name(
                        "name"
                    )
                )

                member_name = (

                    member_name_node.text.decode(
                        "utf-8"
                    )

                    if member_name_node

                    else child.type

                )

                start = (
                    child.start_point[0] + 1
                )

                end = (
                    child.end_point[0] + 1
                )

                summary.append(

                    f"- {member_name} "
                    f"({child.type}, "
                    f"lines {start}-{end})"

                )

        return "\n".join(
            summary
        )
