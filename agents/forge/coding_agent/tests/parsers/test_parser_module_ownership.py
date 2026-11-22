from pathlib import Path

from coding_agent.parsers import build_default_registry


def test_each_extended_parser_has_its_own_module():
    registry = build_default_registry()
    expected = {
        "app.js": "javascript_parser",
        "app.jsx": "jsx_parser",
        "app.ts": "typescript_parser",
        "app.tsx": "tsx_parser",
        "index.html": "html_parser",
        "site.css": "css_parser",
        "site.scss": "scss_parser",
        "schema.sql": "sql_parser",
        "settings.json": "json_parser",
        "settings.yaml": "yaml_parser",
        "settings.toml": "toml_parser",
        "document.xml": "xml_parser",
        "main.go": "go_parser",
        "main.rs": "rust_parser",
        "main.c": "c_parser",
        "main.cpp": "cpp_parser",
        "main.php": "php_parser",
        "main.rb": "ruby_parser",
        "Main.kt": "kotlin_parser",
        "Main.swift": "swift_parser",
        "script.sh": "bash_parser",
        "script.ps1": "powershell_parser",
        "Dockerfile": "dockerfile_parser",
        "main.tf": "terraform_parser",
        "README.md": "markdown_parser",
    }
    for path, module_name in expected.items():
        parser = registry.get_parser(path)
        assert parser is not None, path
        assert parser.__class__.__module__.endswith(module_name)
