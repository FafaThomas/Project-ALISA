
from collections import defaultdict
from pathlib import Path


def generate_codebase_report(codebase_map: dict) -> str:
    """Generate a deterministic Markdown report from a codebase map."""
    summary = codebase_map.get("summary", {})
    nodes = codebase_map.get("nodes", [])
    edges = codebase_map.get("edges", [])
    errors = codebase_map.get("errors", [])
    skipped = codebase_map.get("skipped", [])

    lines = [
        "# Codebase Report",
        "",
        f"**Project root:** `{codebase_map.get('root', 'unknown')}`",
        "",
        "## Overview",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Files inventoried | {summary.get('file_count', len(nodes))} |",
        f"| Symbols discovered | {summary.get('symbol_count', 0)} |",
        f"| Import edges | {summary.get('import_edge_count', len(edges))} |",
        f"| Local imports | {summary.get('local_edge_count', 0)} |",
        f"| External imports | {summary.get('external_edge_count', 0)} |",
        f"| Unresolved imports | {summary.get('unresolved_edge_count', 0)} |",
        f"| Parse errors | {summary.get('parse_error_count', len(errors))} |",
        "",
        "## File Inventory",
        "",
    ]

    sorted_nodes = sorted(
        nodes,
        key=lambda node: node.get("path", "").lower(),
    )

    if not sorted_nodes:
        lines.append("No files were recorded in the codebase map.")
    else:
        for node in sorted_nodes:
            path = node.get("path", "unknown")
            language = node.get("language", "unknown")
            status = node.get("status", "unknown")
            size = node.get("size_bytes", 0)

            lines.append(
                f"- `{path}` — {language}; {size:,} bytes; status: {status}"
            )

    symbol_entries = []
    for node in sorted_nodes:
        for symbol in node.get("symbols", []):
            symbol_entries.append({
                "path": node.get("path", "unknown"),
                "name": symbol.get("name", "unknown"),
                "qualified_name": symbol.get(
                    "qualified_name",
                    symbol.get("name", "unknown"),
                ),
                "kind": symbol.get("kind", "unknown"),
                "line": symbol.get("line"),
                "end_line": symbol.get("end_line"),
            })

    lines.extend(["", "## Symbol Index", ""])

    symbol_entries.sort(
        key=lambda item: (
            item["path"].lower(),
            item["line"] or 0,
            item["qualified_name"].lower(),
        )
    )

    if not symbol_entries:
        lines.append("No symbols were recorded in the codebase map.")
    else:
        for item in symbol_entries:
            location = f"`{item['path']}`"
            if item["line"] is not None:
                location += f":{item['line']}"
                if item["end_line"] is not None:
                    location += f"-{item['end_line']}"

            lines.append(
                f"- **{item['kind']}** `{item['qualified_name']}` — "
                f"{location}"
            )

    lines.extend(["", "## Dependency Overview", ""])

    edges_by_source = defaultdict(list)
    for edge in edges:
        edges_by_source[edge.get("source_path", "unknown")].append(edge)

    if not edges:
        lines.append("No import edges were recorded.")
    else:
        for source_path in sorted(edges_by_source, key=str.lower):
            lines.append(f"### `{source_path}`")
            lines.append("")

            source_edges = sorted(
                edges_by_source[source_path],
                key=lambda edge: edge.get("line", 0),
            )

            for edge in source_edges:
                imported_module = edge.get("imported_module", "unknown")
                imported_names = edge.get("imported_names", [])
                names = ", ".join(imported_names)
                classification = edge.get("classification", "unknown")
                status = edge.get("status", "unknown")
                line = edge.get("line")
                location = f"line {line}: " if line is not None else ""

                description = f"- {location}`{imported_module}`"
                if names:
                    description += f" (names: {names})"

                description += f" — {classification}, {status}"
                lines.append(description)

            lines.append("")

    lines.extend([
        "## Scan Issues",
        "",
        f"- Recorded errors: {len(errors)}",
        f"- Recorded skipped items: {len(skipped)}",
        "",
    ])

    if errors:
        lines.append("### Errors")
        lines.append("")
        for error in errors:
            lines.append(f"- {error}")
        lines.append("")

    if skipped:
        lines.append("### Skipped Items")
        lines.append("")
        for item in skipped:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend([
        "## Interpretation Notes",
        "",
        "- This report is generated from the saved static codebase map.",
        "- Import edges describe detected import relationships, not proof "
        "that imported symbols execute at runtime.",
        "- Dynamic imports, runtime-generated dependencies, and behavior "
        "not represented in the map may be absent.",
        "- The report reflects the map's scan snapshot and may become stale "
        "when source files change.",
        "",
    ])

    return "\n".join(lines)



def write_codebase_report(codebase_map: dict, output: Path) -> Path:
    """Write a Markdown report and return its resolved output path."""
    import os
    import tempfile

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    report = generate_codebase_report(codebase_map)

    # Create the temporary file in the same directory so replacement
    # stays on the same filesystem.
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    temporary_output = Path(temporary_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(report)
            file.flush()
            os.fsync(file.fileno())

        # The file handle is closed before replacement.
        os.replace(temporary_output, output)
    finally:
        if temporary_output.exists():
            temporary_output.unlink()

    return output

