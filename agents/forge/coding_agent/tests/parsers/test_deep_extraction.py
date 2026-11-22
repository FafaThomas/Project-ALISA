from __future__ import annotations

from types import SimpleNamespace

from coding_agent.parsers.javascript_family import JavaScriptFamilyParser
from coding_agent.parsers.sql_parser import SQLParser
from coding_agent.parsers.html_parser import HTMLParser
from coding_agent.parsers.css_parser import CSSParser
from coding_agent.parsers.scss_parser import SCSSParser


class _FakeRoot:
    def __init__(self, has_error: bool = False) -> None:
        self.has_error = has_error


class _FakeTree:
    def __init__(self, has_error: bool = False) -> None:
        self.root_node = _FakeRoot(has_error)


class _FakeTreeSitter:
    def __init__(self, has_error: bool = False) -> None:
        self.has_error = has_error

    def parse(self, _source: bytes) -> _FakeTree:
        return _FakeTree(self.has_error)


class _TextNode:
    def __init__(self, node_type: str, text: str) -> None:
        self.type = node_type
        self.start_byte = 0
        self.end_byte = len(text.encode("utf-8"))
        self.start_point = SimpleNamespace(row=0)
        self._text = text


def test_javascript_import_extracts_default_and_named_bindings():
    parser = JavaScriptFamilyParser()
    source = 'import React, { useState as useLocalState, useEffect } from "react";'
    record = parser._parse_import(
        _TextNode("import_statement", source),
        source.encode("utf-8"),
    )

    assert record is not None
    assert record.module == "react"
    assert record.imported_names == ("default", "useState", "useEffect")
    assert record.kind == "import"
    assert record.is_type_only is False


def test_typescript_import_records_type_only_semantics():
    parser = JavaScriptFamilyParser()
    source = 'import type { User, Account as UserAccount } from "./types";'
    record = parser._parse_import(
        _TextNode("import_statement", source),
        source.encode("utf-8"),
    )

    assert record is not None
    assert record.module == "./types"
    assert record.imported_names == ("User", "Account")
    assert record.is_type_only is True


def test_javascript_dynamic_and_commonjs_imports_are_distinguished():
    parser = JavaScriptFamilyParser()

    dynamic = 'import("./lazy.js")'
    dynamic_record = parser._parse_import(
        _TextNode("call_expression", dynamic),
        dynamic.encode("utf-8"),
    )
    commonjs = 'require("./legacy.js")'
    commonjs_record = parser._parse_import(
        _TextNode("call_expression", commonjs),
        commonjs.encode("utf-8"),
    )

    assert dynamic_record is not None and dynamic_record.kind == "dynamic"
    assert dynamic_record.module == "./lazy.js"
    assert commonjs_record is not None and commonjs_record.kind == "commonjs"
    assert commonjs_record.module == "./legacy.js"


def test_sql_extracts_tables_columns_foreign_keys_ctes_and_table_references():
    parser = SQLParser()
    parser._parser = _FakeTreeSitter()
    source = """CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    department_id INTEGER REFERENCES departments(id),
    display_name TEXT
);

WITH active_employees AS (
    SELECT id FROM employees
)
SELECT * FROM active_employees JOIN departments ON 1 = 1;
"""

    parsed = parser.parse("schema.sql", source)

    symbols = {(symbol.kind, symbol.qualified_name) for symbol in parsed.symbols}
    assert ("table", "departments") in symbols
    assert ("table", "employees") in symbols
    assert ("column", "departments.id") in symbols
    assert ("column", "departments.name") in symbols
    assert ("column", "employees.department_id") in symbols
    assert ("constraint", "employees.department_id_foreign_key") in symbols
    assert ("constraint", "departments.id_primary_key") in symbols
    assert ("cte", "active_employees") in symbols

    references = {(reference.kind, reference.target) for reference in parsed.references}
    assert ("foreign_key", "departments") in references
    assert ("table_reference", "employees") in references
    assert ("table_reference", "departments") in references


def test_sql_ignores_keywords_inside_comments_and_string_literals():
    parser = SQLParser()
    parser._parser = _FakeTreeSitter()
    source = """-- CREATE TABLE fake_table (id INT);
CREATE TABLE real_table (message TEXT DEFAULT 'JOIN fake_table');
"""

    parsed = parser.parse("schema.sql", source)
    names = {symbol.qualified_name for symbol in parsed.symbols}

    assert "real_table" in names
    assert "real_table.message" in names
    assert "fake_table" not in names


def test_sql_rejects_tree_sitter_syntax_errors():
    parser = SQLParser()
    parser._parser = _FakeTreeSitter(has_error=True)

    try:
        parser.parse("broken.sql", "CREATE TABLE broken (")
    except SyntaxError as exc:
        assert "broken.sql" in str(exc)
    else:
        raise AssertionError("Expected SQL syntax errors to be rejected")


def test_javascript_reexports_preserve_source_export_names():
    parser = JavaScriptFamilyParser()
    source = 'export { User as PublicUser, Account } from "./models";'
    record = parser._parse_import(
        _TextNode("export_statement", source),
        source.encode("utf-8"),
    )

    assert record is not None
    assert record.kind == "re-export"
    assert record.module == "./models"
    assert record.imported_names == ("User", "Account")


class _FakeNode:
    def __init__(self, node_type: str, text: str = "", children=(), row: int = 0, start_byte: int = 0):
        self.type = node_type
        self._text = text
        self.start_byte = start_byte
        self.end_byte = start_byte + len(text.encode("utf-8"))
        self.start_point = SimpleNamespace(row=row)
        self.end_point = SimpleNamespace(row=row)
        self.named_children = list(children)
        self.parent = None

    def child_by_field_name(self, _field):
        return None


def _attach_fake_tree(parser, children=()):
    root = _FakeNode("document", children=children)
    parser._parser = SimpleNamespace(
        parse=lambda _source: SimpleNamespace(
            root_node=SimpleNamespace(
                type="document",
                has_error=False,
                named_children=list(children),
                child_by_field_name=lambda _field: None,
                start_point=SimpleNamespace(row=0),
                end_point=SimpleNamespace(row=0),
                parent=None,
            )
        )
    )
    return root


def test_html_extracts_ids_classes_and_asset_references_from_attribute_nodes():
    source = '<main id="app" class="shell primary"><script src="/assets/app.js"></script><link href="styles.css"></main>'
    attrs_text = ['id="app"', 'class="shell primary"', 'src="/assets/app.js"', 'href="styles.css"']
    attrs = [
        _FakeNode("attribute", text, row=index, start_byte=source.index(text))
        for index, text in enumerate(attrs_text, start=1)
    ]
    parser = HTMLParser()
    _attach_fake_tree(parser, attrs)

    parsed = parser.parse("index.html", source)
    symbols = {(item.kind, item.name) for item in parsed.symbols}
    references = {(item.kind, item.target) for item in parsed.references}

    assert ("html_id", "app") in symbols
    assert ("html_class", "shell") in symbols
    assert ("html_class", "primary") in symbols
    assert ("html_asset", "/assets/app.js") in references
    assert ("html_asset", "styles.css") in references


def test_css_extracts_custom_properties_without_comment_false_positives():
    parser = CSSParser()
    _attach_fake_tree(parser)
    source = """/* --fake: red; */
:root { --brand-color: #fff; }
.card { color: var(--brand-color); }
"""

    parsed = parser.parse("site.css", source)
    names = {item.name for item in parsed.symbols}

    assert "--brand-color" in names
    assert "--fake" not in names


def test_scss_extracts_variables_mixins_functions_and_include_references():
    parser = SCSSParser()
    _attach_fake_tree(parser)
    source = """$brand: blue;
@mixin button-base { padding: 1rem; }
@function spacing($n) { @return $n * 1rem; }
.card { @include button-base; }
"""

    parsed = parser.parse("site.scss", source)
    symbols = {(item.kind, item.name) for item in parsed.symbols}
    references = {(item.kind, item.target) for item in parsed.references}

    assert ("scss_variable", "$brand") in symbols
    assert ("mixin", "button-base") in symbols
    assert ("function", "spacing") in symbols
    assert ("mixin_include", "button-base") in references


def test_sql_extracts_named_table_constraints_and_index_targets():
    parser = SQLParser()
    parser._parser = _FakeTreeSitter()
    source = """CREATE TABLE child (
    id INTEGER,
    parent_id INTEGER,
    CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES parent(id),
    UNIQUE (parent_id)
);
CREATE INDEX ix_child_parent ON child(parent_id);
"""

    parsed = parser.parse("relationships.sql", source)
    symbols = {(item.kind, item.qualified_name) for item in parsed.symbols}
    references = {(item.kind, item.target) for item in parsed.references}

    assert ("constraint", "child.fk_parent") in symbols
    assert any(kind == "constraint" and name.startswith("child.unique_") for kind, name in symbols)
    assert ("index", "ix_child_parent") in symbols
    assert ("foreign_key", "parent") in references
    assert ("index_target", "child") in references
