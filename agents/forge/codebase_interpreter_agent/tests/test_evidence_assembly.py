
import hashlib
from pathlib import Path

from src.codebase_interpreter_agent.evidence_assembly import (
    assemble_symbol_evidence,
)

from unittest.mock import patch

import src.codebase_interpreter_agent.evidence_assembly as evidence_assembly


def test_assembles_source_evidence_for_symbol(tmp_path: Path):
    content = (
        "class Example:\n"
        "    def run(self):\n"
        "        return True\n"
    )
    source = tmp_path / "sample.py"
    source.write_text(content, encoding="utf-8", newline="")

    codebase_map = {
        "nodes": [
            {
                "path": "sample.py",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "symbols": [
                    {
                        "name": "Example",
                        "qualified_name": "Example",
                        "kind": "class",
                        "path": "sample.py",
                        "line": 1,
                        "end_line": 3,
                    }
                ],
            }
        ],
        "edges": [],
    }

    result = assemble_symbol_evidence(
        codebase_map,
        tmp_path,
        "Example",
    )

    assert result["status"] == "assembled"
    assert result["symbol"]["qualified_name"] == "Example"
    assert result["evidence_count"] == 1
    assert result["evidence"][0]["type"] == "source"
    assert "class Example:" in result["evidence"][0]["snippet"]
    assert result["evidence"][0]["stale"] is False
    assert result["interpretation"] is None


def test_returns_no_match_for_unknown_symbol(tmp_path: Path):
    result = assemble_symbol_evidence(
        {"nodes": [], "edges": []},
        tmp_path,
        "MissingSymbol",
    )

    assert result["status"] == "no_match"
    assert result["evidence"] == []


def test_rejects_nonpositive_limits(tmp_path: Path):
    result = assemble_symbol_evidence(
        {"nodes": [], "edges": []},
        tmp_path,
        "Example",
        max_evidence_items=0,
    )

    assert result["status"] == "invalid_limit"


def test_prioritizes_source_then_references_then_dependencies(
    tmp_path: Path,
):
    source = tmp_path / "sample.py"
    source.write_text(
        "class Example:\n"
        "    def run(self):\n"
        "        return True\n",
        encoding="utf-8",
        newline="",
    )

    codebase_map = {
        "nodes": [
            {
                "path": "sample.py",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "symbols": [
                    {
                        "name": "Example",
                        "qualified_name": "Example",
                        "kind": "class",
                        "path": "sample.py",
                        "line": 1,
                        "end_line": 3,
                    }
                ],
            }
        ],
        "edges": [],
    }

    reference = {
        "path": "test_sample.py",
        "line": 10,
        "end_line": 10,
        "expression": "obj.Example()",
        "kind": "method_or_attribute_call",
        "confidence": "syntactic_attribute_match",
    }

    dependent = {
        "source_path": "consumer.py",
        "line": 1,
        "imported_module": "sample",
        "imported_names": ["Example"],
        "target_paths": ["sample.py"],
        "status": "resolved",
    }

    with (
        patch.object(
            evidence_assembly,
            "analyze_project_references",
            return_value={
                "status": "found",
                "references": [reference],
                "errors": [],
                "scan_errors": [],
            },
        ),
        patch.object(
            evidence_assembly,
            "find_dependents",
            return_value={"dependents": [dependent]},
        ),
    ):
        result = assemble_symbol_evidence(
            codebase_map,
            tmp_path,
            "Example",
        )

    assert [item["type"] for item in result["evidence"]] == [
        "source",
        "symbol_reference",
        "module_dependency",
    ]


def test_evidence_limit_preserves_source_and_prioritizes_references(
    tmp_path: Path,
):
    source = tmp_path / "sample.py"
    source.write_text(
        "class Example:\n"
        "    def run(self):\n"
        "        return True\n",
        encoding="utf-8",
        newline="",
    )

    codebase_map = {
        "nodes": [
            {
                "path": "sample.py",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "symbols": [
                    {
                        "name": "Example",
                        "qualified_name": "Example",
                        "kind": "class",
                        "path": "sample.py",
                        "line": 1,
                        "end_line": 3,
                    }
                ],
            }
        ],
        "edges": [],
    }

    references = [
        {
            "path": f"test_{index}.py",
            "line": index,
            "end_line": index,
            "expression": "obj.Example()",
            "kind": "method_or_attribute_call",
            "confidence": "syntactic_attribute_match",
        }
        for index in range(10, 15)
    ]

    with (
        patch.object(
            evidence_assembly,
            "analyze_project_references",
            return_value={
                "status": "found",
                "references": references,
                "errors": [],
                "scan_errors": [],
            },
        ),
        patch.object(
            evidence_assembly,
            "find_dependents",
            return_value={
                "dependents": [
                    {
                        "source_path": "consumer.py",
                        "line": 1,
                        "imported_module": "sample",
                        "imported_names": ["Example"],
                        "target_paths": ["sample.py"],
                        "status": "resolved",
                    }
                ]
            },
        ),
    ):
        result = assemble_symbol_evidence(
            codebase_map,
            tmp_path,
            "Example",
            max_evidence_items=3,
        )

    assert result["evidence_count"] == 3
    assert [item["type"] for item in result["evidence"]] == [
        "source",
        "symbol_reference",
        "symbol_reference",
    ]
    assert result["evidence"][0]["stale"] is False



def test_symbol_reference_evidence_preserves_freshness_metadata(
    tmp_path: Path,
):
    source = tmp_path / "sample.py"
    source.write_text(
        "class Example:\n"
        "    def run(self):\n"
        "        return True\n",
        encoding="utf-8",
        newline="",
    )

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    codebase_map = {
        "nodes": [
            {
                "path": "sample.py",
                "sha256": source_hash,
                "symbols": [
                    {
                        "name": "Example",
                        "qualified_name": "Example",
                        "kind": "class",
                        "path": "sample.py",
                        "line": 1,
                        "end_line": 3,
                    }
                ],
            }
        ],
        "edges": [],
    }

    reference = {
        "path": "consumer.py",
        "line": 12,
        "end_line": 12,
        "expression": "obj.Example()",
        "kind": "method_or_attribute_call",
        "confidence": "syntactic_attribute_match",
        "sha256": "current-file-hash",
        "scanned_sha256": "scan-time-file-hash",
        "stale": True,
    }

    with patch.object(
        evidence_assembly,
        "analyze_project_references",
        return_value={
            "status": "found",
            "references": [reference],
            "errors": [],
            "scan_errors": [],
        },
    ):
        result = assemble_symbol_evidence(
            codebase_map,
            tmp_path,
            "Example",
        )

    reference_evidence = next(
        item
        for item in result["evidence"]
        if item["type"] == "symbol_reference"
    )

    assert reference_evidence["sha256"] == "current-file-hash"
    assert reference_evidence["scanned_sha256"] == "scan-time-file-hash"
    assert reference_evidence["stale"] is True


def test_includes_line_numbered_call_site_source(
    tmp_path: Path,
):
    definition = tmp_path / "sample.py"
    definition.write_text(
        "class Example:\n"
        "    def run(self):\n"
        "        return True\n",
        encoding="utf-8",
        newline="",
    )

    consumer = tmp_path / "consumer.py"
    consumer.write_text(
        "def caller(obj):\n"
        "    return obj.Example()\n",
        encoding="utf-8",
        newline="",
    )

    codebase_map = {
        "nodes": [
            {
                "path": "sample.py",
                "sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
                "symbols": [
                    {
                        "name": "Example",
                        "qualified_name": "Example",
                        "kind": "class",
                        "path": "sample.py",
                        "line": 1,
                        "end_line": 3,
                    }
                ],
            },
            {
                "path": "consumer.py",
                "sha256": hashlib.sha256(consumer.read_bytes()).hexdigest(),
                "symbols": [],
            },
        ],
        "edges": [],
    }

    reference = {
        "path": "consumer.py",
        "line": 2,
        "end_line": 2,
        "expression": "obj.Example",
        "kind": "method_or_attribute_call",
        "confidence": "syntactic_attribute_match",
        "sha256": hashlib.sha256(consumer.read_bytes()).hexdigest(),
        "scanned_sha256": hashlib.sha256(consumer.read_bytes()).hexdigest(),
        "stale": False,
    }

    with patch.object(
        evidence_assembly,
        "analyze_project_references",
        return_value={
            "status": "found",
            "references": [reference],
            "errors": [],
            "scan_errors": [],
        },
    ):
        result = assemble_symbol_evidence(
            codebase_map,
            tmp_path,
            "Example",
        )

    snippet_evidence = next(
        item
        for item in result["evidence"]
        if item["type"] == "symbol_reference_source"
    )

    assert snippet_evidence["path"] == "consumer.py"
    assert snippet_evidence["start_line"] == 1
    assert snippet_evidence["end_line"] == 2
    assert snippet_evidence["line_count"] == 2
    assert "   1: def caller(obj):" in snippet_evidence["snippet"]
    assert "   2:     return obj.Example()" in snippet_evidence["snippet"]
    assert snippet_evidence["stale"] is False
    assert result["total_source_lines"] == 5


def test_call_site_source_respects_total_line_budget(
    tmp_path: Path,
):
    definition = tmp_path / "sample.py"
    definition.write_text(
        "class Example:\n"
        "    def run(self):\n"
        "        return True\n",
        encoding="utf-8",
        newline="",
    )

    consumer = tmp_path / "consumer.py"
    consumer.write_text(
        "def caller(obj):\n"
        "    return obj.Example()\n",
        encoding="utf-8",
        newline="",
    )

    codebase_map = {
        "nodes": [
            {
                "path": "sample.py",
                "sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
                "symbols": [
                    {
                        "name": "Example",
                        "qualified_name": "Example",
                        "kind": "class",
                        "path": "sample.py",
                        "line": 1,
                        "end_line": 3,
                    }
                ],
            },
            {
                "path": "consumer.py",
                "sha256": hashlib.sha256(consumer.read_bytes()).hexdigest(),
                "symbols": [],
            },
        ],
        "edges": [],
    }

    reference = {
        "path": "consumer.py",
        "line": 2,
        "end_line": 2,
        "expression": "obj.Example",
        "kind": "method_or_attribute_call",
        "confidence": "syntactic_attribute_match",
        "sha256": hashlib.sha256(consumer.read_bytes()).hexdigest(),
        "scanned_sha256": hashlib.sha256(consumer.read_bytes()).hexdigest(),
        "stale": False,
    }

    with patch.object(
        evidence_assembly,
        "analyze_project_references",
        return_value={
            "status": "found",
            "references": [reference],
            "errors": [],
            "scan_errors": [],
        },
    ):
        result = assemble_symbol_evidence(
            codebase_map,
            tmp_path,
            "Example",
            max_total_lines=4,
        )

    
    assert result["total_source_lines"] == 4
    assert result["total_source_lines"] <= 4

    snippet_evidence = next(
        item
        for item in result["evidence"]
        if item["type"] == "symbol_reference_source"
    )

    assert snippet_evidence["line_count"] == 1
    assert snippet_evidence["start_line"] == 2
    assert snippet_evidence["end_line"] == 2
    assert "return obj.Example()" in snippet_evidence["snippet"]



