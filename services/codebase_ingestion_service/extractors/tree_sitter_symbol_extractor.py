from abc import ABC, abstractmethod

from tree_sitter import Node

from extractors.base_extractor import BaseExtractor


class TreeSitterSymbolExtractor(
    BaseExtractor,
    ABC
):

    def extract(self, tree):

        symbols = []

        stack = []

        self.walk(
            tree.root_node,
            symbols,
            stack,
        )

        return symbols

    def walk(
        self,
        node: Node,
        symbols,
        stack,
    ):

        symbol = None

        if self.is_symbol(node):

            symbol = self.create_symbol(node)

            if symbol:

                #
                # Build hierarchy
                #

                symbol.depth = len(stack)

                if stack:

                    symbol.parent = stack[-1].qualified_name

                    symbol.qualified_name = (
                        f"{stack[-1].qualified_name}.{symbol.name}"
                    )

                else:

                    symbol.parent = None

                    symbol.qualified_name = symbol.name

                symbols.append(symbol)

                stack.append(symbol)

        for child in node.children:

            self.walk(
                child,
                symbols,
                stack,
            )

        if symbol:

            stack.pop()

    @abstractmethod
    def is_symbol(
        self,
        node: Node,
    ):

        pass

    @abstractmethod
    def create_symbol(
        self,
        node: Node,
    ):

        pass