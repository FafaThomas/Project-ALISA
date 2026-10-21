
from __future__ import annotations

import hashlib
from pathlib import Path


DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_SNIPPET_LINES = 500


def _find_nodes(codebase_map: dict, relative_path: str) -> list[dict]:
    return [
        node
        for node in codebase_map.get("nodes", [])
        if node.get("path") == relative_path
    ]


def _resolve_source_path(
    project_root: str | Path,
    relative_path: str,
) -> tuple[Path | None, str | None]:
    root = Path(project_root).resolve()

    if not root.is_dir():
        return None, f"Project root is not a directory: {root}"

    relative = Path(relative_path)

    if relative.is_absolute() or ".." in relative.parts:
        return None, "Source path must be relative to the project root."

    candidate = root / relative

    # Reject symlinks in any path component, including the file itself.
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None, "Source paths containing symlinks are not allowed."

    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None, "Source file does not exist inside the project root."

    if not resolved.is_file():
        return None, "Source path is not a regular file."

    return resolved, None


def retrieve_source(
    codebase_map: dict,
    project_root: str | Path,
    relative_path: str,
    start_line: int,
    end_line: int,
    *,
    context_lines: int = 0,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_snippet_lines: int = DEFAULT_MAX_SNIPPET_LINES,
) -> dict:
    """Retrieve a bounded source range with line numbers and freshness metadata."""
    if start_line < 1 or end_line < start_line:
        return {
            "status": "invalid_range",
            "error": "Expected 1 <= start_line <= end_line.",
        }

    if context_lines < 0:
        return {
            "status": "invalid_range",
            "error": "context_lines cannot be negative.",
        }

    if max_file_bytes <= 0 or max_snippet_lines <= 0:
        return {
            "status": "invalid_limit",
            "error": "File and snippet limits must be positive.",
        }

    nodes = _find_nodes(codebase_map, relative_path)
    if not nodes:
        return {
            "status": "not_indexed",
            "error": "The requested path is not present in the codebase map.",
            "path": relative_path,
        }

    resolved, error = _resolve_source_path(project_root, relative_path)
    if error:
        return {
            "status": "source_unavailable",
            "error": error,
            "path": relative_path,
        }

    try:
        size = resolved.stat().st_size
        if size > max_file_bytes:
            return {
                "status": "file_too_large",
                "path": relative_path,
                "size_bytes": size,
                "max_file_bytes": max_file_bytes,
            }

        content = resolved.read_bytes()
        source = content.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "status": "source_unavailable",
            "path": relative_path,
            "error": f"{type(exc).__name__}: {exc}",
        }

    lines = source.splitlines()
    if start_line > len(lines):
        return {
            "status": "invalid_range",
            "path": relative_path,
            "error": (
                f"Start line {start_line} exceeds the current file length "
                f"of {len(lines)} lines."
            ),
        }

    actual_start = max(1, start_line - context_lines)
    actual_end = min(len(lines), end_line + context_lines)

    if actual_end - actual_start + 1 > max_snippet_lines:
        return {
            "status": "snippet_too_large",
            "path": relative_path,
            "requested_start_line": actual_start,
            "requested_end_line": actual_end,
            "max_snippet_lines": max_snippet_lines,
        }

    current_hash = hashlib.sha256(content).hexdigest()
    indexed_hash = nodes[0].get("sha256") or None
    stale = indexed_hash is not None and indexed_hash != current_hash

    snippet = "\n".join(
        f"{number:4}: {lines[number - 1]}"
        for number in range(actual_start, actual_end + 1)
    )

    return {
        "status": "retrieved",
        "path": relative_path,
        "start_line": actual_start,
        "end_line": actual_end,
        "snippet": snippet,
        "line_count": actual_end - actual_start + 1,
        "stale": stale,
        "indexed_sha256": indexed_hash,
        "current_sha256": current_hash,
        "warning": (
            "The source differs from the indexed snapshot; rescan before "
            "relying on the map's symbol locations."
            if stale
            else None
        ),
    }



def retrieve_symbol_source(
    codebase_map: dict,
    project_root: str | Path,
    symbol_name: str,
    *,
    context_lines: int = 0,
    max_snippet_lines: int = DEFAULT_MAX_SNIPPET_LINES,
) -> dict:
    """Retrieve a uniquely identified symbol using its indexed source range."""
    qualified_matches = []
    name_matches = []

    for node in codebase_map.get("nodes", []):
        for symbol in node.get("symbols", []):
            if symbol.get("qualified_name") == symbol_name:
                qualified_matches.append((node, symbol))
            elif symbol.get("name") == symbol_name:
                name_matches.append((node, symbol))

    # Prefer exact qualified-name matches over short-name matches.
    matches = qualified_matches if qualified_matches else name_matches

    if not matches:
        return {
            "status": "no_match",
            "symbol": symbol_name,
            "evidence": [],
        }

    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "symbol": symbol_name,
            "evidence": [
                {
                    "path": node.get("path"),
                    "qualified_name": symbol.get("qualified_name"),
                    "line": symbol.get("line"),
                    "end_line": symbol.get("end_line"),
                }
                for node, symbol in matches
            ],
        }

    node, symbol = matches[0]
    result = retrieve_source(
        codebase_map,
        project_root,
        node["path"],
        symbol["line"],
        symbol["end_line"],
        context_lines=context_lines,
        max_snippet_lines=max_snippet_lines,
    )

    result["symbol"] = symbol.get("qualified_name", symbol_name)
    result["kind"] = symbol.get("kind")
    return result

