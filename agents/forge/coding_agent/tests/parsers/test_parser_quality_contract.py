from coding_agent.parsers.base import ParseDiagnostic, ParseFailure
from coding_agent.parsers.registry import ParserRegistry
from coding_agent.parsers.defaults import build_default_registry


def test_parse_failure_preserves_multiple_structured_diagnostics():
    diagnostics = (
        ParseDiagnostic("Unexpected token", 2, 4, code="treesitter.syntax-error"),
        ParseDiagnostic("Missing ) token", 3, 8, code="treesitter.missing-token"),
    )
    failure = ParseFailure("Malformed source", diagnostics)
    assert str(failure) == "Malformed source"
    assert failure.diagnostics == diagnostics


def test_cpp_and_c_are_both_candidates_for_h_headers():
    registry = build_default_registry()
    candidates = registry.get_candidates("include/types.h")
    assert {parser.language for parser in candidates} == {"c", "cpp"}
    # Backward-compatible default remains deterministic.
    assert registry.get_parser("include/types.h").language == "c"
    assert registry.get_parser("include/types.h", language="cpp").language == "cpp"


def test_unknown_language_override_does_not_silently_choose_another_parser():
    registry = build_default_registry()
    assert registry.get_parser("include/types.h", language="rust") is None


def test_code_index_retains_python_syntax_error_location(tmp_path):
    from coding_agent.code_index import CodeIndexer
    from coding_agent.workspace import Workspace

    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    index = CodeIndexer(Workspace(tmp_path)).build()
    module = index.modules[0]
    assert module.parse_error is not None
    assert module.diagnostics
    assert module.diagnostics[0].line == 1
    assert module.diagnostics[0].column >= 1
    assert module.diagnostics[0].code == "python.syntax-error"


def test_code_index_can_select_cpp_for_ambiguous_h_extension(tmp_path):
    from coding_agent.code_index import CodeIndexer
    from coding_agent.workspace import Workspace

    (tmp_path / "types.h").write_text("class Widget {};\n", encoding="utf-8")
    index = CodeIndexer(Workspace(tmp_path), parser_language_overrides={".h": "cpp"}).build()
    assert index.modules[0].language == "cpp"


def test_tree_sitter_diagnostic_collector_reports_error_and_missing_nodes():
    from types import SimpleNamespace
    from coding_agent.parsers.treesitter_parser import TreeSitterParser

    class Node:
        def __init__(self, node_type, row, column, *, missing=False, children=()):
            self.type = node_type
            self.start_point = SimpleNamespace(row=row, column=column)
            self.end_point = SimpleNamespace(row=row, column=column + 1)
            self.is_missing = missing
            self.children = list(children)

    root = Node("source_file", 0, 0, children=[
        Node("ERROR", 1, 3),
        Node(")", 2, 7, missing=True),
    ])
    diagnostics = TreeSitterParser._collect_syntax_diagnostics(root)
    assert [item.code for item in diagnostics] == [
        "treesitter.syntax-error", "treesitter.missing-token"
    ]
    assert [(item.line, item.column) for item in diagnostics] == [(2, 4), (3, 8)]


def test_markdown_parser_extracts_links_images_and_code_blocks():
    from coding_agent.parsers.markdown_parser import MarkdownParser

    parser = MarkdownParser()
    source = "See [guide](docs/guide.md) and ![logo](img/logo.svg).\n\n```python\nprint('hi')\n```\n\n[ref]: docs/ref.md\n"
    symbols = parser._extract_additional_symbols(None, relative_path="README.md", source_bytes=source.encode())
    refs = parser._extract_references(None, source.encode())
    assert any(item.kind == "code_block" and item.declared_type == "python" for item in symbols)
    assert {(item.kind, item.target) for item in refs} == {
        ("markdown_link", "docs/guide.md"),
        ("markdown_image", "img/logo.svg"),
        ("markdown_reference", "docs/ref.md"),
    }


def test_dockerfile_parser_extracts_stages_and_stage_references():
    from coding_agent.parsers.dockerfile_parser import DockerfileParser

    parser = DockerfileParser()
    source = "FROM python:3.12 AS builder\nCOPY --from=builder /app /app\nFROM alpine:3.20\n"
    symbols = parser._extract_additional_symbols(None, relative_path="Dockerfile", source_bytes=source.encode())
    refs = parser._extract_references(None, source.encode())
    assert [(item.kind, item.name, item.declared_type) for item in symbols] == [
        ("build_stage", "builder", "python:3.12")
    ]
    assert set((item.kind, item.target) for item in refs) == {
        ("docker_base_image", "python:3.12"),
        ("docker_stage_reference", "builder"),
        ("docker_base_image", "alpine:3.20"),
    }
