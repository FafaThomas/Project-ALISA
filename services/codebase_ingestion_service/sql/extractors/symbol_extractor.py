from sql.models.sql_table_symbol import SQLTableSymbol
from sql.models.sql_view_symbol import SQLViewSymbol
from sql.models.sql_function_symbol import SQLFunctionSymbol
from sql.models.sql_procedure_symbol import SQLProcedureSymbol
from sql.models.sql_trigger_symbol import SQLTriggerSymbol
from sql.models.sql_sequence_symbol import SQLSequenceSymbol
from sql.models.sql_index_symbol import SQLIndexSymbol


class SQLSymbolExtractor:

    def extract(self, parse_result):

        tree = parse_result.tree

        if tree is None:
            return []

        root = tree.root_node

        symbols = []

        symbols.extend(self._extract_tables(root))
        symbols.extend(self._extract_views(root))
        symbols.extend(self._extract_functions(root))
        symbols.extend(self._extract_procedures(root))
        symbols.extend(self._extract_triggers(root))
        symbols.extend(self._extract_sequences(root))
        symbols.extend(self._extract_indexes(root))

        print("\n================ SQL SYMBOLS ================\n")

        for symbol in symbols:

            print(symbol.model_dump())

        print("\n=============================================\n")

        return symbols


    ############################################################
    # Helpers
    ############################################################

    def _iter_statements(self, root):

        for node in root.children:

            if node.type == "statement":
                yield node


    def _get_object_name(self, create_node):

        for node in create_node.children:

            if node.type != "object_reference":
                continue

            for child in node.children:

                if child.type == "identifier":

                    return child.text.decode("utf-8")

        return None
    

    def _create_symbol(
        self,
        symbol_cls,
        name,
        kind,
        node,
    ):

        return symbol_cls(

            name=name,
            qualified_name=name,
            kind=kind,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent=None,
            depth=0,
            schema_name=None,

        )


    ############################################################
    # Extractors
    ############################################################

    def _extract_tables(self, root):

        symbols = []

        for statement in self._iter_statements(root):

            for node in statement.children:

                if node.type != "create_table":
                    continue

                table_name = self._get_object_name(node)

                if table_name is None:
                    continue

                symbols.append(

                    self._create_symbol(
                        SQLTableSymbol,
                        table_name,
                        "table",
                        node,
                    )

                )

        return symbols


    def _extract_views(self, root):

        symbols = []

        for statement in self._iter_statements(root):

            for node in statement.children:

                if node.type != "create_view":
                    continue

                view_name = self._get_object_name(node)

                if view_name is None:
                    continue

                symbols.append(

                    self._create_symbol(
                        SQLViewSymbol,
                        view_name,
                        "view",
                        node,
                    )

                )

        return symbols


    def _extract_functions(self, root):

        symbols = []

        for statement in self._iter_statements(root):

            for node in statement.children:

                if node.type != "create_function":
                    continue

                function_name = self._get_object_name(node)

                if function_name is None:
                    continue

                symbols.append(

                    self._create_symbol(
                        SQLFunctionSymbol,
                        function_name,
                        "function",
                        node,
                    )

                )

        return symbols


    def _extract_procedures(self, root):

        symbols = []

        for statement in self._iter_statements(root):

            for node in statement.children:

                if node.type != "create_procedure":
                    continue

                procedure_name = self._get_object_name(node)

                if procedure_name is None:
                    continue

                symbols.append(

                    self._create_symbol(
                        SQLProcedureSymbol,
                        procedure_name,
                        "procedure",
                        node,
                    )

                )

        return symbols


    def _extract_triggers(self, root):

        symbols = []

        for statement in self._iter_statements(root):

            for node in statement.children:

                if node.type != "create_trigger":
                    continue

                trigger_name = self._get_object_name(node)

                if trigger_name is None:
                    continue

                symbols.append(

                    self._create_symbol(
                        SQLTriggerSymbol,
                        trigger_name,
                        "trigger",
                        node,
                    )

                )

        return symbols


    def _extract_sequences(self, root):

        symbols = []

        for statement in self._iter_statements(root):

            for node in statement.children:

                if node.type != "create_sequence":
                    continue

                sequence_name = self._get_object_name(node)

                if sequence_name is None:
                    continue

                symbols.append(

                    self._create_symbol(
                        SQLSequenceSymbol,
                        sequence_name,
                        "sequence",
                        node,
                    )

                )

        return symbols

    def _extract_indexes(self, root):

        symbols = []

        for statement in self._iter_statements(root):

            for node in statement.children:

                if node.type != "create_index":
                    continue

                index_name = self._get_object_name(node)

                if index_name is None:
                    continue

                symbols.append(

                    self._create_symbol(
                        SQLIndexSymbol,
                        index_name,
                        "index",
                        node,
                    )

                )

        return symbols