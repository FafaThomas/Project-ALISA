from abc import ABC, abstractmethod

from tree_sitter import Node

from extractors.base_call_extractor import (
    BaseCallExtractor,
)

from models.call import Call


class TreeSitterCallExtractor(
    BaseCallExtractor,
    ABC,
):

    def extract(self, parse_result):

        self.source = parse_result.source.raw_source

        calls = []

        self.walk(
            parse_result.tree.root_node,
            None,
            calls,
        )

        return calls

    def walk(
        self,
        node: Node,
        current_scope,
        calls,
    ):

        if self.is_scope(node):

            current_scope = self.get_scope_name(node)

        if self.is_call(node):

            call = self.create_call(
                node,
                current_scope,
            )

            if call:

                calls.append(call)

        for child in node.children:

            self.walk(
                child,
                current_scope,
                calls,
            )

    def text(
        self,
        node: Node,
    ):

        return self.source[
            node.start_byte:node.end_byte
        ]

    @abstractmethod
    def is_scope(
        self,
        node: Node,
    ):

        pass

    @abstractmethod
    def get_scope_name(
        self,
        node: Node,
    ):

        pass

    @abstractmethod
    def is_call(
        self,
        node: Node,
    ):

        pass

    @abstractmethod
    def create_call(
        self,
        node: Node,
        current_scope,
    ) -> Call | None:

        pass