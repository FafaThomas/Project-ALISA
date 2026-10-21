
from pathlib import Path

from src.codebase_interpreter_agent.reference_analysis import (
    analyze_python_references,
)
import ast

from src.codebase_interpreter_agent.scanner import (
    FileRecord,
    ScanReport,
)

import hashlib

from codebase_interpreter_agent import reference_analysis


def test_finds_function_and_attribute_calls(tmp_path: Path):
    source = tmp_path / "example.py"
    source.write_text(
        "run_for_current_agent(root)\n"
        "pipeline.run_for_current_agent(root)\n",
        encoding="utf-8",
    )

    result = analyze_python_references(
        tmp_path,
        "example.py",
        "run_for_current_agent",
    )

    assert result["status"] == "found"
    assert result["reference_count"] == 2
    assert result["references"][0]["kind"] == "function_call"
    assert result["references"][0]["line"] == 1
    assert result["references"][1]["kind"] == "method_or_attribute_call"
    assert result["references"][1]["line"] == 2


def test_returns_no_match(tmp_path: Path):
    (tmp_path / "example.py").write_text(
        "value = 42\n",
        encoding="utf-8",
    )

    result = analyze_python_references(
        tmp_path,
        "example.py",
        "missing_function",
    )

    assert result["status"] == "no_match"
    assert result["references"] == []


def test_rejects_path_traversal(tmp_path: Path):
    result = analyze_python_references(
        tmp_path,
        "../outside.py",
        "run",
    )

    assert result["status"] == "invalid_path"
    assert result["references"] == []


def test_rejects_non_python_files(tmp_path: Path):
    (tmp_path / "example.js").write_text(
        "run();\n",
        encoding="utf-8",
    )

    result = analyze_python_references(
        tmp_path,
        "example.js",
        "run",
    )

    assert result["status"] == "unsupported_language"


def test_analyzes_references_across_project(tmp_path, monkeypatch):
    from codebase_interpreter_agent import reference_analysis

    source = tmp_path / "example.py"
    source.write_text(
        "pipeline.run_for_current_agent(root)\n",
        encoding="utf-8",
    )

    report = ScanReport(
        root=str(tmp_path),
        files=[
            FileRecord(
                path="example.py",
                language="python",
                size_bytes=source.stat().st_size,
                sha256="test-hash",
                status="analyzed",
            )
        ],
    )

    monkeypatch.setattr(
        reference_analysis,
        "scan_project",
        lambda *args, **kwargs: report,
    )

    result = reference_analysis.analyze_project_references(
        tmp_path,
        "run_for_current_agent",
    )

    assert result["status"] == "found"
    assert result["reference_count"] == 1
    assert result["references"][0]["path"] == "example.py"
    assert result["references"][0]["line"] == 1


def test_rejects_empty_project_reference_query(tmp_path):
    from src.codebase_interpreter_agent.reference_analysis import (
        analyze_project_references,
    )

    result = analyze_project_references(tmp_path, "   ")

    assert result["status"] == "invalid_query"
    assert result["references"] == []


def test_project_reference_is_fresh_when_hash_matches_scan(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "example.py"
    source.write_text(
        "pipeline.run_for_current_agent(root)\n",
        encoding="utf-8",
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    report = ScanReport(
        root=str(tmp_path),
        files=[
            FileRecord(
                path="example.py",
                language="python",
                size_bytes=source.stat().st_size,
                sha256=source_hash,
                status="analyzed",
            )
        ],
    )

    monkeypatch.setattr(
        reference_analysis,
        "scan_project",
        lambda *args, **kwargs: report,
    )

    result = reference_analysis.analyze_project_references(
        tmp_path,
        "run_for_current_agent",
    )

    reference = result["references"][0]
    assert reference["stale"] is False
    assert reference["sha256"] == source_hash
    assert reference["scanned_sha256"] == source_hash
    assert result["freshness_warnings"] == []


def test_project_reference_is_stale_when_file_changes_after_scan(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "example.py"
    original_content = "pipeline.run_for_current_agent(root)\n"
    source.write_text(original_content, encoding="utf-8")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    report = ScanReport(
        root=str(tmp_path),
        files=[
            FileRecord(
                path="example.py",
                language="python",
                size_bytes=source.stat().st_size,
                sha256=original_hash,
                status="analyzed",
            )
        ],
    )

    # Simulate a source edit after the scan recorded its hash.
    source.write_text(
        "pipeline.other_method(root)\n"
        "pipeline.run_for_current_agent(root)\n",
        encoding="utf-8",
    )
    changed_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    monkeypatch.setattr(
        reference_analysis,
        "scan_project",
        lambda *args, **kwargs: report,
    )

    result = reference_analysis.analyze_project_references(
        tmp_path,
        "run_for_current_agent",
    )

    assert result["status"] == "found"
    assert result["reference_count"] == 1

    reference = result["references"][0]
    assert reference["stale"] is True
    assert reference["scanned_sha256"] == original_hash
    assert reference["sha256"] == changed_hash
    assert changed_hash != original_hash
    assert result["freshness_warnings"] == [
        {
            "path": "example.py",
            "scanned_sha256": original_hash,
            "current_sha256": changed_hash,
        }
    ]

