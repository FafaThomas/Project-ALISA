from models.call_graph import CallGraph


class CallGraphBuilder:

    def build(self, parsed_documents):

        calls = []

        for document in parsed_documents:

            calls.extend(document.calls)

        return CallGraph(calls=calls)