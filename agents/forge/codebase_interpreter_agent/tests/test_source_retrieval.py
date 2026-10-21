
import hashlib
from pathlib import Path

from src.codebase_interpreter_agent.source_retrieval import (
    retrieve_source,
    retrieve_symbol_source,
)



def _make_map(root: Path) -> dict:
    source = root / "sample.py"
    content = (
        "class Example:\n"
        "    def run(self):\n"
        "        return True\n"
    )
    source.write_text(content, encoding="utf-8", newline="")

    actual_bytes = source.read_bytes()

    return {
        "root": str(root),
        "nodes": [
            {
                "path": "sample.py",
                "sha256": hashlib.sha256(actual_bytes).hexdigest(),
                "symbols": [
                    {
                        "path": "sample.py",
                        "name": "Example",
                        "qualified_name": "Example",
                        "kind": "class",
                        "line": 1,
                        "end_line": 3,
                    }
                ],
            }
        ],
    }



def test_retrieves_symbol_with_line_numbers(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = retrieve_symbol_source(
        codebase_map,
        tmp_path,
        "Example",
    )

    assert result["status"] == "retrieved"
    assert result["path"] == "sample.py"
    assert result["start_line"] == 1
    assert result["end_line"] == 3
    assert "class Example:" in result["snippet"]
    assert "   2:     def run(self):" in result["snippet"]
    assert result["stale"] is False


def test_detects_stale_source(tmp_path: Path):
    codebase_map = _make_map(tmp_path)
    (tmp_path / "sample.py").write_text(
        "class Example:\n    pass\n",
        encoding="utf-8",
    )

    result = retrieve_symbol_source(
        codebase_map,
        tmp_path,
        "Example",
    )

    assert result["status"] == "retrieved"
    assert result["stale"] is True
    assert result["warning"] is not None


def test_rejects_paths_outside_project(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = retrieve_source(
        codebase_map,
        tmp_path,
        "../outside.py",
        1,
        1,
    )

    assert result["status"] == "not_indexed"


def test_returns_no_match_for_unknown_symbol(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = retrieve_symbol_source(
        codebase_map,
        tmp_path,
        "MissingSymbol",
    )

    assert result["status"] == "no_match"
    assert result["evidence"] == []


def test_short_name_is_ambiguous_when_multiple_symbols_match(
    tmp_path: Path,
):
    source_a = "class Alpha:\n    def run(self):\n        pass\n"
    source_b = "class Beta:\n    def run(self):\n        pass\n"

    (tmp_path / "alpha.py").write_text(source_a, encoding="utf-8")
    (tmp_path / "beta.py").write_text(source_b, encoding="utf-8")

    codebase_map = {
        "nodes": [
            {
                "path": "alpha.py",
                "sha256": hashlib.sha256(
                    (tmp_path / "alpha.py").read_bytes()
                ).hexdigest(),
                "symbols": [
                    {
                        "name": "run",
                        "qualified_name": "Alpha.run",
                        "kind": "function",
                        "line": 2,
                        "end_line": 3,
                    }
                ],
            },
            {
                "path": "beta.py",
                "sha256": hashlib.sha256(
                    (tmp_path / "beta.py").read_bytes()
                ).hexdigest(),
                "symbols": [
                    {
                        "name": "run",
                        "qualified_name": "Beta.run",
                        "kind": "function",
                        "line": 2,
                        "end_line": 3,
                    }
                ],
            },
        ]
    }

    result = retrieve_symbol_source(
        codebase_map,
        tmp_path,
        "run",
    )

    assert result["status"] == "ambiguous"
    assert len(result["evidence"]) == 2

