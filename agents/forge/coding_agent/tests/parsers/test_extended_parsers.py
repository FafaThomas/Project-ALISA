from __future__ import annotations

import pytest

from coding_agent.parsers import build_default_registry
from coding_agent.workspace import Workspace
from coding_agent.code_index import CodeIndexer


def test_default_registry_resolves_extended_languages_and_formats():
    registry = build_default_registry()

    expected = {
        "app.js": "javascript",
        "app.jsx": "jsx",
        "app.ts": "typescript",
        "app.tsx": "tsx",
        "index.html": "html",
        "site.css": "css",
        "site.scss": "scss",
        "schema.sql": "sql",
        "settings.json": "json",
        "settings.yaml": "yaml",
        "settings.yml": "yaml",
        "pyproject.toml": "toml",
        "document.xml": "xml",
        "main.go": "go",
        "main.rs": "rust",
        "main.c": "c",
        "main.cpp": "cpp",
        "main.php": "php",
        "main.rb": "ruby",
        "Main.kt": "kotlin",
        "Main.swift": "swift",
        "script.sh": "bash",
        "script.ps1": "powershell",
        "main.tf": "terraform",
        "README.md": "markdown",
    }

    for path, language in expected.items():
        parser = registry.get_parser(path)
        assert parser is not None, path
        assert parser.language == language, path

    assert registry.get_parser("Dockerfile").language == "dockerfile"
    assert registry.get_parser("Containerfile").language == "dockerfile"


def test_registry_extension_lookup_remains_case_insensitive():
    registry = build_default_registry()

    assert registry.get_parser("src/App.TSX").language == "tsx"
    assert registry.get_parser("src/CONFIG.YML").language == "yaml"
    assert registry.get_parser("src/MAIN.CPP").language == "cpp"


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("app.js", "export function greet(name) { return name; }"),
        ("app.jsx", "export const App = () => <main>Hello</main>;"),
        ("app.ts", "interface User { id: number; }"),
        ("app.tsx", "const App = () => <div>Hello</div>;"),
        ("index.html", "<html><body><h1>Hello</h1></body></html>"),
        ("site.css", ".card { color: red; }"),
        ("site.scss", ".card { color: red; }"),
        ("schema.sql", "CREATE TABLE users (id INTEGER PRIMARY KEY);"),
        ("settings.json", '{"name": "ALISA", "enabled": true}'),
        ("settings.yaml", "name: ALISA\nenabled: true\n"),
        ("pyproject.toml", '[project]\nname = "alisa"\n'),
        ("document.xml", '<root><item id="1"/></root>'),
        ("main.go", "package main\nfunc main() {}\n"),
        ("main.rs", "fn main() {}\n"),
        ("main.c", "int main(void) { return 0; }"),
        ("main.cpp", "class Engine {};"),
        ("main.php", "<?php function greet() {}"),
        ("main.rb", "class Engine\n def run; end\nend"),
        ("Main.kt", "class Engine { fun run() {} }"),
        ("Main.swift", "struct Engine { func run() {} }"),
        ("script.sh", "function greet() { echo hi; }"),
        ("script.ps1", "function Get-Thing { return 1 }"),
        ("Dockerfile", "FROM python:3.12\nWORKDIR /app\n"),
        ("main.tf", 'resource "example" "demo" { name = "ALISA" }'),
        ("README.md", "# ALISA\n\nForge coding agent.\n"),
    ],
)
def test_extended_parser_loads_and_parses_smoke_source(path, source):
    parser = build_default_registry().get_parser(path)
    assert parser is not None

    parsed = parser.parse(path, source)

    assert parsed is not None
    assert isinstance(parsed.symbols, tuple)
    assert isinstance(parsed.imports, tuple)


def test_code_index_discovers_mixed_language_workspace(tmp_path):
    files = {
        "src/main.py": "def main():\n    return 1\n",
        "web/App.tsx": "const App = () => <div>Hello</div>;\n",
        "web/index.html": "<html><body><div id='app'></div></body></html>",
        "database/schema.sql": "CREATE TABLE users (id INTEGER PRIMARY KEY);",
        "Dockerfile": "FROM python:3.12\n",
        "config/settings.yaml": "name: ALISA\n",
    }

    for relative_path, source in files.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    index = CodeIndexer(Workspace(tmp_path)).build()

    assert index.discovered_files == len(files)
    assert index.indexed_files == len(files)
    assert index.failed_files == 0

    languages = {module.language for module in index.modules}
    assert languages == {
        "python", "tsx", "html", "sql", "dockerfile", "yaml"
    }

    rendered = index.render()
    assert "CODEBASE STRUCTURE" in rendered
    assert "Languages:" in rendered


def test_jsx_extracts_react_component_exports_import_bindings_and_component_refs():
    parser = build_default_registry().get_parser("App.jsx")
    source = '''import React, { useState as useLocalState } from "react";

export const App = () => <UserCard />;
'''

    parsed = parser.parse("App.jsx", source)

    app = next(symbol for symbol in parsed.symbols if symbol.name == "App")
    assert app.kind == "function"
    assert app.is_exported is True
    assert app.role == "react_component"
    assert parsed.imports[0].module == "react"
    assert parsed.imports[0].imported_names == ("default", "useState")
    assert any(
        reference.kind == "jsx_component" and reference.target == "UserCard"
        for reference in parsed.references
    )


def test_typescript_extracts_interfaces_types_enums_and_type_only_imports():
    parser = build_default_registry().get_parser("service.ts")
    source = '''import type { User } from "./models";

export interface UserService {
    getUser(id: string): User;
}

export type UserId = string;
export enum Status { Active, Disabled }
'''

    parsed = parser.parse("service.ts", source)
    kinds = {(symbol.kind, symbol.name) for symbol in parsed.symbols}

    assert ("interface", "UserService") in kinds
    assert ("method", "getUser") in kinds
    assert ("type", "UserId") in kinds
    assert ("enum", "Status") in kinds
    assert parsed.imports[0].is_type_only is True
    assert parsed.imports[0].imported_names == ("User",)
    assert next(s for s in parsed.symbols if s.name == "UserService").is_exported


def test_sql_extracts_ddl_columns_and_foreign_key_references():
    parser = build_default_registry().get_parser("schema.sql")
    source = '''CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL
);
CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    department_id INTEGER REFERENCES departments(id)
);
'''

    parsed = parser.parse("schema.sql", source)
    symbols = {(symbol.kind, symbol.qualified_name) for symbol in parsed.symbols}

    assert ("table", "departments") in symbols
    assert ("column", "departments.name") in symbols
    assert ("table", "employees") in symbols
    assert ("column", "employees.department_id") in symbols
    assert any(
        reference.kind == "foreign_key" and reference.target == "departments"
        for reference in parsed.references
    )


def test_html_extracts_nested_elements_ids_classes_and_asset_links():
    parser = build_default_registry().get_parser("index.html")
    source = '''<html><body><main id="app" class="shell primary">
<script src="/assets/app.js"></script><link href="styles.css">
</main></body></html>'''

    parsed = parser.parse("index.html", source)
    symbols = {(symbol.kind, symbol.name) for symbol in parsed.symbols}

    assert ("element", "html") in symbols
    assert ("html_id", "app") in symbols
    assert ("html_class", "shell") in symbols
    assert ("html_class", "primary") in symbols
    assert {reference.target for reference in parsed.references} >= {
        "/assets/app.js", "styles.css"
    }


def test_css_and_scss_extract_custom_properties_and_abstractions():
    registry = build_default_registry()
    css = registry.get_parser("site.css").parse(
        "site.css", ":root { --brand: blue; }\n.card { color: var(--brand); }"
    )
    scss = registry.get_parser("site.scss").parse(
        "site.scss",
        "$brand: blue;\n@mixin button-base { padding: 1rem; }\n.card { @include button-base; }",
    )

    assert any(symbol.kind == "custom_property" and symbol.name == "--brand" for symbol in css.symbols)
    assert any(symbol.kind == "scss_variable" and symbol.name == "$brand" for symbol in scss.symbols)
    assert any(symbol.kind == "mixin" and symbol.name == "button-base" for symbol in scss.symbols)
    assert any(reference.kind == "mixin_include" and reference.target == "button-base" for reference in scss.references)
