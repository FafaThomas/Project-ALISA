
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from codebase_interpreter_agent.scanner import scan_project


def analyze_python_references(
    project_root: str | Path,
    relative_path: str,
    target_name: str,
) -> dict:
    """Find syntactic references and fingerprint the analyzed source file.

    This reports syntactic matches, not proof of runtime dispatch or type.
    """
    root = Path(project_root).resolve()
    relative = Path(relative_path)

    if relative.is_absolute() or ".." in relative.parts:
        return {
            "status": "invalid_path",
            "references": [],
            "errors": ["Path must be relative to the project root."],
        }

    path = root / relative

    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return {
            "status": "invalid_path",
            "references": [],
            "errors": [
                "Source path is missing or escapes the project root."
            ],
        }

    if path.is_symlink() or not resolved.is_file():
        return {
            "status": "invalid_path",
            "references": [],
            "errors": [
                "Source must be a regular, non-symlink file."
            ],
        }

    if resolved.suffix.lower() not in {".py", ".pyi"}:
        return {
            "status": "unsupported_language",
            "references": [],
            "errors": [
                "Reference analysis currently supports Python only."
            ],
        }

    try:
        source_bytes = resolved.read_bytes()
        current_sha256 = hashlib.sha256(source_bytes).hexdigest()
        source = source_bytes.decode("utf-8-sig")
        tree = ast.parse(source, filename=relative.as_posix())
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        return {
            "status": "analysis_error",
            "references": [],
            "errors": [str(exc)],
        }

    references = []

    class ReferenceVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            reference = None

            if isinstance(func, ast.Name) and func.id == target_name:
                reference = {
                    "kind": "function_call",
                    "name": target_name,
                    "line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "expression": ast.unparse(func),
                    "confidence": "syntactic_name_match",
                }

            elif (
                isinstance(func, ast.Attribute)
                and func.attr == target_name
            ):
                reference = {
                    "kind": "method_or_attribute_call",
                    "name": target_name,
                    "line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "expression": ast.unparse(func),
                    "confidence": "syntactic_attribute_match",
                }

            if reference is not None:
                references.append(reference)

            self.generic_visit(node)

    ReferenceVisitor().visit(tree)
    references.sort(key=lambda item: (item["line"], item["kind"]))

    return {
        "status": "found" if references else "no_match",
        "path": relative.as_posix(),
        "query": target_name,
        "sha256": current_sha256,
        "reference_count": len(references),
        "references": references,
        "errors": [],
        "limitations": [
            "Name matches do not prove runtime identity or receiver type.",
            "Only call expressions are included in this initial version.",
        ],
    }


def analyze_project_references(
    project_root: str | Path,
    target_name: str,
    *,
    max_file_bytes: int = 5 * 1024 * 1024,
) -> dict:
    """Find syntactic call references across the scanned Python project."""
    root = Path(project_root).resolve()

    if not target_name or not target_name.strip():
        return {
            "status": "invalid_query",
            "query": target_name,
            "references": [],
            "reference_count": 0,
            "errors": ["Target name cannot be empty."],
        }

    try:
        scan_report = scan_project(
            root,
            max_file_bytes=max_file_bytes,
            verbose=False,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "analysis_error",
            "query": target_name,
            "references": [],
            "reference_count": 0,
            "errors": [str(exc)],
        }

    references = []
    errors = []
    freshness_warnings = []

    for file_record in scan_report.files:
        if file_record.language != "python":
            continue

        if file_record.status != "analyzed":
            errors.append({
                "path": file_record.path,
                "error": f"File was not analyzed: {file_record.status}",
            })
            continue

        result = analyze_python_references(
            root,
            file_record.path,
            target_name.strip(),
        )

        if result["status"] == "analysis_error":
            errors.append({
                "path": file_record.path,
                "error": "; ".join(result["errors"]),
            })
            continue

        if result["status"] in {"invalid_path", "unsupported_language"}:
            errors.append({
                "path": file_record.path,
                "error": "; ".join(result.get("errors", [])),
            })
            continue

        scanned_sha256 = getattr(file_record, "sha256", None)
        current_sha256 = result.get("sha256")
        stale = (
            scanned_sha256 is not None
            and current_sha256 != scanned_sha256
        )

        if stale:
            freshness_warnings.append({
                "path": file_record.path,
                "scanned_sha256": scanned_sha256,
                "current_sha256": current_sha256,
            })

        if result["status"] == "found":
            for reference in result["references"]:
                references.append({
                    "path": file_record.path,
                    "sha256": current_sha256,
                    "stale": stale,
                    "scanned_sha256": scanned_sha256,
                    **reference,
                })

    references.sort(
        key=lambda item: (
            item["path"],
            item["line"],
            item["kind"],
        )
    )

    return {
        "status": "found" if references else "no_match",
        "query": target_name.strip(),
        "reference_count": len(references),
        "references": references,
        "files_scanned": sum(
            file_record.language == "python"
            and file_record.status == "analyzed"
            for file_record in scan_report.files
        ),
        "errors": errors,
        "scan_errors": scan_report.errors,
        "freshness_warnings": freshness_warnings,
        "limitations": [
            "References are syntactic call matches, not proof of runtime dispatch.",
            "Attribute calls do not establish the receiver's type.",
            "Only Python files and call expressions are analyzed.",
            "Freshness compares the analyzed file with its scan-time hash.",
        ],
    }
