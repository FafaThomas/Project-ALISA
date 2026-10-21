
import argparse
import json
import sys
from pathlib import Path

from codebase_interpreter_agent.scanner import scan_project
from codebase_interpreter_agent.dependency_resolver import resolve_imports
from codebase_interpreter_agent.codebase_map import build_codebase_map
from codebase_interpreter_agent.codebase_queries import (
    answer_codebase_question,
)
from codebase_interpreter_agent.documentation import (
    write_codebase_report,
)
from codebase_interpreter_agent.source_retrieval import (
    retrieve_symbol_source,
)
from codebase_interpreter_agent.evidence_assembly import (
    assemble_symbol_evidence,
)

from codebase_interpreter_agent.interpretation import (
    interpret_codebase_question,
)


def _load_codebase_map(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(
            f"Codebase map not found: {path}\n"
            "Run the scan command to generate it."
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict) or "nodes" not in data:
        raise ValueError(f"Invalid codebase map format: {path}")

    return data


def _run_scan(
    root: Path,
    output: Path,
    quiet: bool = False,
) -> int:
    root = root.resolve()
    output = output.resolve()

    if not root.is_dir():
        raise NotADirectoryError(
            f"Project directory does not exist: {root}"
        )

    # Avoid accidentally including the generated map in the scan.
    if output == root or root not in output.parents:
        raise ValueError(
            "The output map must be a file inside the project directory."
        )

    print(f"Scanning project: {root}")

    scan_report = scan_project(
        root,
        verbose=not quiet,
    )
    dependency_report = resolve_imports(scan_report)
    codebase_map = build_codebase_map(
        scan_report,
        dependency_report,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_name(output.name + ".tmp")

    try:
        with temporary_output.open("w", encoding="utf-8") as file:
            json.dump(codebase_map.to_dict(), file, indent=2)
            file.write("\n")
            file.flush()

        temporary_output.replace(output)
    finally:
        if temporary_output.exists():
            temporary_output.unlink()

    summary = codebase_map.summary

    print(f"\nCodebase map written to: {output}")
    print(f"Files: {summary.get('file_count', 0)}")
    print(f"Symbols: {summary.get('symbol_count', 0)}")
    print(f"Import edges: {summary.get('import_edge_count', 0)}")
    print(f"Parse errors: {summary.get('parse_error_count', 0)}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="codebase-interpreter",
        description="Scan and query ALISA codebases.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    query_parser = subparsers.add_parser(
        "query",
        help="Ask a question about an indexed codebase.",
    )
    query_parser.add_argument("question")
    query_parser.add_argument(
        "--map",
        dest="map_path",
        type=Path,
        default=Path("codebase_map.json"),
        help="Path to codebase_map.json.",
    )
    query_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete result as JSON.",
    )

    query_parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Project root used to retrieve source for LLM interpretation. "
            "Defaults to the root recorded in the map, if available."
        ),
    )

    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan a project and generate its codebase map.",
    )
    scan_parser.add_argument(
        "root",
        type=Path,
        help="Root directory of the project to scan.",
    )
    scan_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output map path (default: ROOT/codebase_map.json).",
    )
    scan_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress detailed scanner progress logs.",
    )

    docs_parser = subparsers.add_parser(
        "docs",
        help="Generate a Markdown report from a codebase map.",
    )
    docs_parser.add_argument(
        "--map",
        dest="map_path",
        type=Path,
        default=Path("codebase_map.json"),
        help="Path to the codebase map.",
    )
    docs_parser.add_argument(
        "--output",
        type=Path,
        default=Path("CODEBASE_REPORT.md"),
        help="Path for the generated Markdown report.",
    )

    source_parser = subparsers.add_parser(
        "source",
        help="Retrieve the source code for an indexed symbol.",
    )
    source_parser.add_argument(
        "symbol",
        help="Symbol name or qualified name to retrieve.",
    )
    source_parser.add_argument(
        "--map",
        dest="map_path",
        type=Path,
        default=Path("codebase_map.json"),
        help="Path to the codebase map.",
    )
    source_parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Root directory of the source project.",
    )
    source_parser.add_argument(
        "--context-lines",
        type=int,
        default=0,
        help="Number of surrounding lines to include on each side.",
    )
    source_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete result as JSON.",
    )

    evidence_parser = subparsers.add_parser(
        "evidence",
        help="Assemble source-backed evidence for an indexed symbol.",
    )
    evidence_parser.add_argument(
        "symbol",
        help="Symbol name or qualified name to investigate.",
    )
    evidence_parser.add_argument(
        "--map",
        dest="map_path",
        type=Path,
        default=Path("codebase_map.json"),
        help="Path to the codebase map.",
    )
    evidence_parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Root directory of the source project.",
    )
    evidence_parser.add_argument(
        "--context-lines",
        type=int,
        default=0,
        help="Number of surrounding source lines to include.",
    )
    evidence_parser.add_argument(
        "--max-evidence-items",
        type=int,
        default=12,
        help="Maximum number of evidence entries.",
    )
    evidence_parser.add_argument(
        "--max-total-lines",
        type=int,
        default=1200,
        help="Maximum source lines to retrieve.",
    )
    evidence_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete result as JSON.",
    )


    args = parser.parse_args()

    try:
        if args.command == "scan":
            output = args.output or (args.root / "codebase_map.json")
            return _run_scan(
                args.root,
                output,
                quiet=args.quiet,
            )

        if args.command == "docs":
            codebase_map = _load_codebase_map(args.map_path)
            output = write_codebase_report(
                codebase_map,
                args.output,
            )
            print(f"Documentation written to: {output}")
            return 0

        if args.command == "source":
            if args.context_lines < 0:
                raise ValueError("--context-lines cannot be negative.")

            codebase_map = _load_codebase_map(args.map_path)

            result = retrieve_symbol_source(
                codebase_map,
                args.root,
                args.symbol,
                context_lines=args.context_lines,
            )

            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Status: {result['status']}")

                if result.get("path"):
                    print(f"File: {result['path']}")

                if result.get("symbol"):
                    print(f"Symbol: {result['symbol']}")

                if result.get("start_line") is not None:
                    print(
                        f"Lines: {result['start_line']}-"
                        f"{result['end_line']}"
                    )

                if result.get("stale") is not None:
                    print(f"Stale: {result['stale']}")

                if result.get("warning"):
                    print(f"Warning: {result['warning']}")

                if result.get("error"):
                    print(f"Error: {result['error']}")

                if result.get("evidence"):
                    print("\nMatches:")
                    for item in result["evidence"]:
                        print(
                            f"  - {item.get('path')} "
                            f"({item.get('qualified_name')}, "
                            f"lines {item.get('line')}-"
                            f"{item.get('end_line')})"
                        )

                if result.get("snippet"):
                    print("\nSource:")
                    print(result["snippet"])

            return 0 if result["status"] == "retrieved" else 1

        if args.command == "evidence":
            if args.context_lines < 0:
                raise ValueError("--context-lines cannot be negative.")

            codebase_map = _load_codebase_map(args.map_path)

            result = assemble_symbol_evidence(
                codebase_map,
                args.root,
                args.symbol,
                context_lines=args.context_lines,
                max_evidence_items=args.max_evidence_items,
                max_total_lines=args.max_total_lines,
            )

            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Status: {result['status']}")
                print(f"Query: {result['query']}")

                if result.get("symbol"):
                    symbol = result["symbol"]
                    print(f"Symbol: {symbol['qualified_name']}")
                    print(f"File: {symbol['path']}")
                    print(
                        f"Lines: {symbol['line']}-"
                        f"{symbol['end_line']}"
                    )

                for item in result.get("evidence", []):
                    print(f"\nEvidence type: {item['type']}")

                    if item.get("path"):
                        print(f"File: {item['path']}")

                    if item.get("source_path"):
                        print(f"Source file: {item['source_path']}")

                    if item.get("start_line") is not None:
                        print(
                            f"Lines: {item['start_line']}-"
                            f"{item['end_line']}"
                        )

                    if item.get("snippet"):
                        print(item["snippet"])

                    if item.get("relationship_note"):
                        print(item["relationship_note"])

                    if item.get("stale") is not None:
                        print(f"Stale: {item['stale']}")

                for error in result.get("errors", []):
                    print(f"Warning: {error}")

            return 0 if result["status"] in {
                "assembled",
                "ambiguous",
            } else 1

        codebase_map = _load_codebase_map(args.map_path)
        result = answer_codebase_question(
            codebase_map,
            args.question,
        )

        # Preserve deterministic answers. Use the LLM only when the
        # deterministic query engine cannot adequately answer.
        if result["status"] in {"unsupported", "no_match"}:
            recorded_root = codebase_map.get("root")
            project_root = args.root

            if project_root is None and isinstance(recorded_root, str):
                project_root = Path(recorded_root)

            if project_root is None:
                result = {
                    "status": "root_required",
                    "answer": (
                        "LLM interpretation requires the project root. "
                        "Rerun the query with --root <project-directory>."
                    ),
                    "evidence": [],
                }
            else:
                result = interpret_codebase_question(
                    codebase_map,
                    project_root,
                    args.question,
                )

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Status: {result['status']}")
            print(result["answer"])

            if result.get("evidence"):
                print("\nEvidence:")
                for item in result["evidence"]:
                    path = (
                        item.get("path")
                        or item.get("symbol_path")
                        or item.get("source_path")
                        or "unknown"
                    )
                    line = item.get("line") or item.get("start_line")
                    end_line = item.get("end_line")
                    if line and end_line and end_line != line:
                        location = f"{path}:{line}-{end_line}"
                    else:
                        location = f"{path}:{line}" if line else path
                    print(f"  - {location}")

            for error in result.get("errors", []):
                print(f"Warning: {error}")

        return 0 if result["status"] == "answered" else 1

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
