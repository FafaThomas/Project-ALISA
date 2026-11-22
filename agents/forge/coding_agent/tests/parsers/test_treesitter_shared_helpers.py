from coding_agent.parsers.go_parser import GoParser
from coding_agent.parsers.treesitter_parser import TreeSitterParser


class FakeNode:
    def __init__(self, node_type, start=0, end=0, children=(), fields=None):
        self.type = node_type
        self.start_byte = start
        self.end_byte = end
        self.named_children = list(children)
        self._fields = fields or {}

    def child_by_field_name(self, name):
        return self._fields.get(name)


def test_shared_walk_traverses_named_nodes_in_preorder():
    leaf = FakeNode("identifier", 2, 5)
    child = FakeNode("declaration", 1, 6, [leaf])
    root = FakeNode("source_file", 0, 7, [child])

    assert [node.type for node in TreeSitterParser._walk(root)] == [
        "source_file",
        "declaration",
        "identifier",
    ]


def test_shared_find_descendant_excludes_root_and_finds_nested_node():
    target = FakeNode("variable_declarator", 2, 5)
    root = FakeNode("field_declaration", 0, 6, [target])

    assert TreeSitterParser()._find_descendant(
        root, {"variable_declarator"}
    ) is target
    assert TreeSitterParser()._find_descendant(
        target, {"variable_declarator"}
    ) is None


def test_shared_declarator_name_prefers_declarator_field():
    source = b"int *value"
    identifier = FakeNode("identifier", 5, 10)
    pointer = FakeNode("pointer_declarator", 4, 10, [identifier])
    declaration = FakeNode(
        "init_declarator", 4, 10, [pointer], fields={"declarator": pointer}
    )

    assert TreeSitterParser()._find_declarator_name(
        declaration, source
    ) == "value"


def test_go_package_clause_extracts_package_identifier():
    source = b"package demo"
    package_identifier = FakeNode("package_identifier", 8, 12)
    package_clause = FakeNode("package_clause", 0, 12, [package_identifier])

    assert GoParser()._get_symbol_name(package_clause, source) == "demo"
