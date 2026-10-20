
from pathlib import Path

from codebase_interpreter_agent.scanner import scan_project
from codebase_interpreter_agent.dependency_resolver import resolve_imports
from codebase_interpreter_agent.codebase_map import build_codebase_map
from codebase_interpreter_agent.codebase_queries import (
    find_dependents,
    find_symbol,
)


def _make_map(tmp_path: Path) -> dict:
    package = tmp_path / "src" / "sample_pkg"
    package.mkdir(parents=True)

    (package / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (package / "pipeline.py").write_text(
        "class IngestionPipeline:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "cli.py").write_text(
        "from sample_pkg.pipeline import IngestionPipeline\n"
        "\n"
        "def main():\n"
        "    return IngestionPipeline()\n",
        encoding="utf-8",
    )

    scan = scan_project(tmp_path, verbose=False)
    dependencies = resolve_imports(scan)

    return build_codebase_map(scan, dependencies).to_dict()


def test_finds_symbol_with_source_location(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = find_symbol(
        codebase_map,
        "IngestionPipeline",
    )

    assert result["match_count"] == 1
    match = result["matches"][0]

    assert match["kind"] == "class"
    assert match["path"] == "src/sample_pkg/pipeline.py"
    assert match["line"] == 1


def test_finds_dependents_of_local_module(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = find_dependents(
        codebase_map,
        "src/sample_pkg/pipeline.py",
    )

    assert result["dependent_count"] == 1
    dependent = result["dependents"][0]

    assert dependent["source_path"] == "src/sample_pkg/cli.py"
    assert dependent["line"] == 1
    assert dependent["status"] == "resolved_local"


def test_missing_symbol_returns_no_matches(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = find_symbol(codebase_map, "NonexistentAgent")

    assert result["match_count"] == 0
    assert result["matches"] == []
