from models.call_graph import CallGraph


class CallResolver:

    def resolve(
        self,
        graph: CallGraph,
        parsed_documents
    ) -> CallGraph:

        lookup = {}

        #
        # Build symbol table
        #

        for document in parsed_documents:

            for symbol in document.symbols:

                lookup[symbol.name] = (
                    document.relative_path,
                    symbol.kind
                )

        #
        # Resolve every call
        #

        for call in graph.calls:

            if call.callee in lookup:

                file, kind = lookup[call.callee]

                call.callee_file = file

                call.callee_symbol = kind

        return graph