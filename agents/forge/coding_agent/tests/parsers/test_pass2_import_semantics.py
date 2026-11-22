from types import SimpleNamespace

from coding_agent.parsers import CParser, CppParser, CSharpParser, GoParser, JavaParser, RustParser


class TextNode:
    def __init__(self, node_type, text, row=0, parent=None):
        self.type = node_type
        self._text = text
        self.start_byte = 0
        self.end_byte = len(text.encode("utf-8"))
        self.start_point = SimpleNamespace(row=row)
        self.end_point = SimpleNamespace(row=row)
        self.parent = parent

    def child_by_field_name(self, _name):
        return None


def parse_import(parser, node):
    return parser._parse_import(node, node._text.encode("utf-8"))


def test_go_import_spec_preserves_alias_and_blank_import():
    parser = GoParser()
    aliased = parse_import(parser, TextNode("import_spec", 'alias "example.com/lib"'))
    blank = parse_import(parser, TextNode("import_spec", '_ "image/png"'))

    assert aliased.module == "example.com/lib"
    assert aliased.alias == "alias"
    assert aliased.kind == "go_import"
    assert blank.alias == "_"


def test_rust_use_preserves_leaf_alias_and_wildcard_semantics():
    parser = RustParser()
    leaf = parse_import(parser, TextNode("use_declaration", "use crate::models::User as Account;"))
    wildcard = parse_import(parser, TextNode("use_declaration", "use crate::services::*;"))

    assert leaf.module == "crate::models"
    assert leaf.imported_names == ("User",)
    assert leaf.alias == "Account"
    grouped = parse_import(
        parser,
        TextNode("use_declaration", "pub use crate::models::{User, Account as PublicAccount};"),
    )
    assert grouped.is_public is True
    assert grouped.imported_names == ("User", "Account")
    assert grouped.aliases == (("Account", "PublicAccount"),)
    assert wildcard.module == "crate::services"
    assert wildcard.is_wildcard is True


def test_c_include_distinguishes_system_and_local_headers():
    parser = CParser()
    system = parse_import(parser, TextNode("preproc_include", "#include <stdio.h>"))
    local = parse_import(parser, TextNode("preproc_include", '#include "project.h"'))

    assert system.module == "stdio.h"
    assert system.is_system is True
    assert local.module == "project.h"
    assert local.is_system is False


def test_cpp_include_has_language_specific_kind():
    record = parse_import(CppParser(), TextNode("preproc_include", '#include "engine.hpp"'))
    assert record.module == "engine.hpp"
    assert record.kind == "cpp_include"


def test_csharp_using_preserves_alias_static_and_global():
    parser = CSharpParser()
    alias = parse_import(parser, TextNode("using_directive", "using Collections = System.Collections.Generic;"))
    static = parse_import(parser, TextNode("using_directive", "using static System.Math;"))
    global_using = parse_import(parser, TextNode("using_directive", "global using System;"))

    assert alias.alias == "Collections"
    assert alias.imported_names == ("Collections",)
    assert static.is_static is True
    assert global_using.is_global is True


def test_java_import_preserves_static_and_wildcard_semantics():
    parser = JavaParser()
    static = parse_import(parser, TextNode("import_declaration", "import static java.util.Collections.emptyList;"))
    wildcard = parse_import(parser, TextNode("import_declaration", "import java.util.*;"))

    assert static.module == "java.util.Collections.emptyList"
    assert static.is_static is True
    assert wildcard.is_wildcard is True
