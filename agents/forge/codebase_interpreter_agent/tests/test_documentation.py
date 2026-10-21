
from pathlib import Path

from agents.forge.codebase_interpreter_agent.src.codebase_interpreter_agent.documentation import (
    generate_codebase_report,
    write_codebase_report,
)


def _sample_map() -> dict:
    return {
        "root": "sample-project",
        "nodes": [
            {
                "path": "src/example.py",
                "language": "python",
                "size_bytes": 120,
                "status": "parsed",
                "symbols": [
                    {
                        "name": "Example",
                        "qualified_name": "Example",
                        "kind": "class",
                        "line": 1,
                        "end_line": 10,
                    }
                ],
            }
        ],
        "edges": [
            {
                "source_path": "src/example.py",
                "line": 2,
                "imported_module": "json",
                "imported_names": [],
                "status": "external",
                "classification": "external",
                "target_paths": [],
            }
        ],
        "errors": [],
        "skipped": [],
        "summary": {
            "file_count": 1,
            "symbol_count": 1,
            "import_edge_count": 1,
            "local_edge_count": 0,
            "external_edge_count": 1,
            "unresolved_edge_count": 0,
            "parse_error_count": 0,
        },
    }


def test_report_includes_inventory_symbols_and_dependencies():
    report = generate_codebase_report(_sample_map())

    assert "# Codebase Report" in report
    assert "src/example.py" in report
    assert "`Example`" in report
    assert "`json`" in report
    assert "not proof" in report


def test_report_writer_creates_markdown_file(tmp_path: Path):
    output = tmp_path / "CODEBASE_REPORT.md"

    result = write_codebase_report(_sample_map(), output)

    assert result == output.resolve()
    assert result.is_file()
    assert "# Codebase Report" in result.read_text(encoding="utf-8")
    assert not output.with_name(output.name + ".tmp").exists()
