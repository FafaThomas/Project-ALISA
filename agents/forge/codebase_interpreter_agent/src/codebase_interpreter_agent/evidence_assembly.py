
from __future__ import annotations

from pathlib import Path

from codebase_interpreter_agent.codebase_queries import (
    find_dependents,
    find_symbol,
)
from codebase_interpreter_agent.source_retrieval import (
    retrieve_source,
)

from codebase_interpreter_agent.reference_analysis import (
    analyze_project_references,
)


DEFAULT_MAX_EVIDENCE_ITEMS = 12
DEFAULT_MAX_TOTAL_LINES = 1200


def assemble_symbol_evidence(
    codebase_map: dict,
    project_root: str | Path,
    symbol_name: str,
    *,
    context_lines: int = 0,
    max_evidence_items: int = DEFAULT_MAX_EVIDENCE_ITEMS,
    max_total_lines: int = DEFAULT_MAX_TOTAL_LINES,
) -> dict:
    """Assemble source-backed evidence for a symbol without interpreting it."""
    if max_evidence_items <= 0 or max_total_lines <= 0:
        return {
            "status": "invalid_limit",
            "query": symbol_name,
            "evidence": [],
            "errors": ["Evidence limits must be positive."],
        }

    if context_lines < 0:
        return {
            "status": "invalid_limit",
            "query": symbol_name,
            "evidence": [],
            "errors": ["context_lines cannot be negative."],
        }

    symbol_result = find_symbol(codebase_map, symbol_name)
    matches = symbol_result["matches"]

    if not matches:
        return {
            "status": "no_match",
            "query": symbol_name,
            "evidence": [],
            "errors": [],
        }

    # Qualified-name queries should select the exact symbol when available.
    exact = [
        match
        for match in matches
        if match["qualified_name"] == symbol_name
    ]
    if exact:
        matches = exact

    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "query": symbol_name,
            "evidence": [
                {
                    "type": "symbol_definition",
                    **match,
                }
                for match in matches
            ],
            "errors": [],
        }

    symbol = matches[0]
    evidence = []
    errors = []
    total_lines = 0

    source_result = retrieve_source(
        codebase_map,
        project_root,
        symbol["path"],
        symbol["line"],
        symbol["end_line"],
        context_lines=context_lines,
        max_snippet_lines=max_total_lines,
    )

    if source_result["status"] != "retrieved":
        errors.append({
            "stage": "source_retrieval",
            "path": symbol["path"],
            "error": source_result.get("error", source_result["status"]),
        })
    else:
        evidence.append({
            "type": "source",
            "path": source_result["path"],
            "start_line": source_result["start_line"],
            "end_line": source_result["end_line"],
            "line_count": source_result["line_count"],
            "snippet": source_result["snippet"],
            "stale": source_result["stale"],
            "sha256": source_result["current_sha256"],
            "symbol": symbol["qualified_name"],
        })
        total_lines += source_result["line_count"]

        if source_result.get("warning"):
            errors.append({
                "stage": "source_freshness",
                "path": symbol["path"],
                "error": source_result["warning"],
            })

    # Add import relationships for the file containing the symbol.
    if len(evidence) < max_evidence_items:
        reference_result = analyze_project_references(
            project_root,
            symbol["name"],
        )

        if reference_result["status"] == "analysis_error":
            errors.append({
                "stage": "reference_analysis",
                "error": reference_result.get("errors", []),
            })
        else:
            
            for reference in reference_result.get("references", []):
                if len(evidence) >= max_evidence_items:
                    break

                evidence.append({
                    "type": "symbol_reference",
                    "scope": "syntactic_call",
                    "symbol": symbol["qualified_name"],
                    "path": reference["path"],
                    "line": reference["line"],
                    "end_line": reference["end_line"],
                    "expression": reference["expression"],
                    "reference_kind": reference["kind"],
                    "confidence": reference["confidence"],
                    "sha256": reference.get("sha256"),
                    "stale": reference.get("stale"),
                    "scanned_sha256": reference.get("scanned_sha256"),
                    "relationship_note": (
                        "Syntactic call match only; this does not prove "
                        "the receiver or function resolves to this exact symbol."
                    ),
                })

                # Add source context as separate evidence, respecting both limits.
                remaining_items = max_evidence_items - len(evidence)
                remaining_lines = max_total_lines - total_lines

                if remaining_items <= 0 or remaining_lines <= 0:
                    continue

                
                snippet_result = None

                # Start with two context lines on either side.
                # Reduce context until the snippet fits the remaining budget.
                for context_size in range(2, -1, -1):
                    candidate = retrieve_source(
                        codebase_map,
                        project_root,
                        reference["path"],
                        reference["line"],
                        reference["end_line"],
                        context_lines=context_size,
                        max_snippet_lines=remaining_lines,
                    )

                    snippet_result = candidate

                    if candidate["status"] == "retrieved":
                        break

                    if candidate["status"] != "snippet_too_large":
                        break

                if snippet_result["status"] != "retrieved":
                    errors.append({
                        "stage": "reference_source_retrieval",
                        "path": reference["path"],
                        "line": reference["line"],
                        "error": snippet_result.get(
                            "error",
                            snippet_result["status"],
                        ),
                    })
                    continue


                evidence.append({
                    "type": "symbol_reference_source",
                    "scope": "call_site",
                    "symbol": symbol["qualified_name"],
                    "path": snippet_result["path"],
                    "start_line": snippet_result["start_line"],
                    "end_line": snippet_result["end_line"],
                    "line_count": snippet_result["line_count"],
                    "snippet": snippet_result["snippet"],
                    "sha256": snippet_result["current_sha256"],
                    "stale": snippet_result["stale"],
                    "relationship_note": (
                        "Source context for a syntactic call-site match; "
                        "this does not prove runtime identity."
                    ),
                })

                total_lines += snippet_result["line_count"]

                if snippet_result.get("warning"):
                    errors.append({
                        "stage": "reference_source_freshness",
                        "path": reference["path"],
                        "error": snippet_result["warning"],
                    })

            for error in reference_result.get("errors", []):
                errors.append({
                    "stage": "reference_analysis",
                    "error": error,
                })

            for error in reference_result.get("scan_errors", []):
                errors.append({
                    "stage": "reference_scan",
                    "error": error,
                })

    # Add module-level import relationships for the containing file.
    if len(evidence) < max_evidence_items:
        dependent_result = find_dependents(
            codebase_map,
            symbol["path"],
        )

        for dependent in dependent_result["dependents"]:
            if len(evidence) >= max_evidence_items:
                break

            evidence.append({
                "type": "module_dependency",
                "scope": "file",
                "symbol": symbol["qualified_name"],
                "source_path": dependent["source_path"],
                "line": dependent["line"],
                "imported_module": dependent["imported_module"],
                "imported_names": dependent["imported_names"],
                "target_paths": dependent["target_paths"],
                "status": dependent["status"],
                "relationship_note": (
                    "This file imports the module containing the symbol; "
                    "this does not establish a direct reference to the symbol."
                ),
            })

    return {
        "status": "assembled" if evidence else "evidence_unavailable",
        "query": symbol_name,
        "symbol": symbol,
        "evidence": evidence,
        "evidence_count": len(evidence),
        "total_source_lines": total_lines,
        "limits": {
            "max_evidence_items": max_evidence_items,
            "max_total_lines": max_total_lines,
        },
        "errors": errors,
        "interpretation": None,
    }
