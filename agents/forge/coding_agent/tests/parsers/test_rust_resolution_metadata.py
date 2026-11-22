from types import SimpleNamespace

from coding_agent.parsers.rust_parser import RustParser


class FakeNode:
    def __init__(self, node_type, text):
        self.type = node_type
        self.start_byte = 0
        self.end_byte = len(text.encode("utf-8"))
        self.start_point = SimpleNamespace(row=0)
        self.end_point = SimpleNamespace(row=0)
        self.named_children = []
        self.parent = None

    def child_by_field_name(self, _name):
        return None


def test_rust_nested_use_trees_are_expanded_into_individual_import_records():
    source = "pub use crate::{models::{User, Account as PublicAccount}, services::*};"
    node = FakeNode("use_declaration", source)
    records = RustParser()._parse_imports(node, source.encode())

    assert [(item.module, item.imported_names, item.is_wildcard) for item in records] == [
        ("crate::models", ("User",), False),
        ("crate::models", ("Account",), False),
        ("crate::services", (), True),
    ]
    assert records[0].is_public is True
    assert records[1].aliases == (("Account", "PublicAccount"),)


def test_rust_mod_symbol_records_inline_vs_external_form():
    parser = RustParser()
    external_source = "pub mod models;"
    inline_source = "mod models { }"

    external = parser._build_symbol(
        FakeNode("mod_item", external_source),
        relative_path="src/lib.rs",
        source_bytes=external_source.encode(),
        scopes=[],
        symbol_name="models",
        symbol_kind="module",
    )
    inline = parser._build_symbol(
        FakeNode("mod_item", inline_source),
        relative_path="src/lib.rs",
        source_bytes=inline_source.encode(),
        scopes=[],
        symbol_name="models",
        symbol_kind="module",
    )

    assert dict(external.metadata)["module_form"] == "external"
    assert dict(inline.metadata)["module_form"] == "inline"


def test_rust_use_parser_preserves_simple_leaf_alias_and_absolute_paths():
    parser = RustParser()
    source = "use ::serde::Deserialize as Decode;"
    records = parser._parse_imports(FakeNode("use_declaration", source), source.encode())
    assert len(records) == 1
    assert records[0].module == "serde"
    assert records[0].imported_names == ("Deserialize",)
    assert records[0].alias == "Decode"
    assert records[0].aliases == (("Deserialize", "Decode"),)
