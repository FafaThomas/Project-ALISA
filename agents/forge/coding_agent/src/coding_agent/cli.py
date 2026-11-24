"""Command-line interface for inspecting and querying ALISA workspaces."""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import sys
from uuid import uuid4
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from coding_agent import __version__
from coding_agent.code_index import CodeIndexer
from coding_agent.context_engine import ContextEngine
from coding_agent.knowledge_graph import GraphEdge, GraphNode, KnowledgeGraph
from coding_agent.models import CodingTask, TaskStatus
from coding_agent.workspace import Workspace, WorkspaceError
from coding_agent.change_journal import ChangeJournal
from coding_agent.coordinator import ChangeCoordinator
from coding_agent.command_runner import CommandRunner
from coding_agent.llm_client import OllamaClient
from coding_agent.orchestrator import TaskOrchestrator
from coding_agent.retry import RetryPolicy, RetryRequest
from coding_agent.planner import CodingPlanner
from coding_agent.verification import VerificationEngine


def _workspace(path: str) -> Workspace:
    return Workspace(path)


def _index(path: str, max_files: int) :
    workspace = _workspace(path)
    return workspace, CodeIndexer(workspace, max_files=max_files).build()


def _node_dict(node: GraphNode) -> dict[str, Any]:
    return asdict(node)


def _edge_dict(edge: GraphEdge) -> dict[str, Any]:
    return asdict(edge)


def _emit(value: Any, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    elif isinstance(value, str):
        print(value)
    else:
        print(value)


def _add_workspace_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("workspace", help="Path to the authorized project workspace")
    parser.add_argument("--max-files", type=int, default=2000,
                        help="Maximum source files to index (default: 2000)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-agent",
        description="Inspect, index, and query an ALISA Coding Agent workspace.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build a multi-language code index")
    _add_workspace_options(index_parser)

    graph_parser = subparsers.add_parser("graph", help="Build and display the knowledge graph")
    _add_workspace_options(graph_parser)
    graph_parser.add_argument("--max-nodes", type=int, default=100)
    graph_parser.add_argument("--max-edges", type=int, default=200)

    search_parser = subparsers.add_parser("search", help="Search indexed modules and symbols")
    _add_workspace_options(search_parser)
    search_parser.add_argument("query", help="Text to search for")
    search_parser.add_argument("--kind", help="Filter graph node kind (module, symbol, reference)")
    search_parser.add_argument("--language", help="Filter by language")
    search_parser.add_argument("--path", help="Filter by exact workspace-relative path")
    search_parser.add_argument("--limit", type=int, default=50)

    rel_parser = subparsers.add_parser("relationships", help="List relationships for a matching node")
    _add_workspace_options(rel_parser)
    rel_parser.add_argument("target", help="Node ID or unique/partial symbol label")
    rel_parser.add_argument("--direction", choices=("in", "out", "both"), default="both")
    rel_parser.add_argument("--kind", action="append", dest="kinds",
                            help="Relationship kind filter; may be repeated")
    rel_parser.add_argument("--include-unresolved", action="store_true",
                            help="Include unresolved and ambiguous relationships")

    impact_parser = subparsers.add_parser("impact", help="Find resolved upstream dependents of a node")
    _add_workspace_options(impact_parser)
    impact_parser.add_argument("target", help="Node ID or unique/partial symbol label")
    impact_parser.add_argument("--depth", type=int, default=2, help="Maximum traversal depth")
    impact_parser.add_argument("--kind", action="append", dest="kinds",
                               help="Relationship kind filter; may be repeated")

    context_parser = subparsers.add_parser("context", help="Assemble bounded task context")
    _add_workspace_options(context_parser)
    context_parser.add_argument("description", help="Coding task description")
    context_parser.add_argument("--task-id", default="cli-context")
    context_parser.add_argument("--max-context-files", type=int, default=30)
    context_parser.add_argument("--max-context-chars", type=int, default=60000)

    run_parser = subparsers.add_parser(
        "run", help="Generate, review, approve, and execute a coding task"
    )
    run_parser.add_argument("workspace", help="Path to the authorized project workspace")
    run_parser.add_argument("description", help="Coding task description")
    run_parser.add_argument("--task-id", default=None)
    run_parser.add_argument("--constraint", action="append", default=[],
                             help="Task constraint; may be repeated")
    run_parser.add_argument("--acceptance-criteria", action="append", default=[],
                             help="Acceptance criterion; may be repeated")
    run_parser.add_argument("--max-context-files", type=int, default=30)
    run_parser.add_argument("--max-context-chars", type=int, default=60000)
    run_parser.add_argument("--timeout", type=float, default=120.0,
                             help="Per verification-command timeout in seconds")
    run_parser.add_argument(
        "--llm-timeout", type=float, default=None,
        help="Ollama request socket timeout in seconds (default: CODING_AGENT_LLM_TIMEOUT or 300)",
    )
    run_parser.add_argument("--max-attempts", type=int, default=3,
                             help="Maximum total implementation attempts, including the first (default: 3)")
    run_parser.add_argument(
        "--journal", default=None,
        help="Change journal path (default: .coding-agent/changes.jsonl inside workspace)",
    )
    run_parser.add_argument(
        "--manual-evidence", default=None,
        help=(
            "JSON file of human PASS/FAIL evidence for manual criteria, keyed by criterion ID: "
            '{"VER-001": {"status": "PASS", "evidence": "what you observed"}}. '
            "Criteria not listed stay unverified. Replaces the interactive prompt. "
            "Only used with --verify spec."
        ),
    )
    run_parser.add_argument(
        "--verify", choices=("files", "tests", "spec"), default="files",
        help=(
            "What must pass for the task to complete. files (default): every planned file is "
            "written and Python files compile. tests: also the project's own tests pass. "
            "spec: every Engineering Specification criterion passes. Checks outside the "
            "chosen level still run and are reported as advisory."
        ),
    )

    return parser


def _resolve_target(graph: KnowledgeGraph, target: str) -> GraphNode:
    node = graph.get_node(target)
    if node is not None:
        return node
    matches = graph.find_nodes(target)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"No graph node matched: {target}")
    choices = "\n".join(f"  {item.label} [{item.id}]" for item in matches[:20])
    raise ValueError(
        f"'{target}' matched {len(matches)} nodes. Use an exact node ID:\n{choices}"
    )


def _display_plan(plan, workspace: Workspace) -> None:
    """Show the exact proposed file operations and verification commands."""
    print("\nIMPLEMENTATION PLAN")
    print("=" * 72)
    print(plan.summary)
    if plan.risks:
        print("\nRisks / assumptions:")
        for risk in plan.risks:
            print(f"  - {risk}")

    for index, change in enumerate(plan.changes, start=1):
        print(f"\n[{index}] {change.change_type.value.upper()} {change.path}")
        print(f"Reason: {change.reason}")
        try:
            if change.change_type.value == "create":
                before = ""
            else:
                before = workspace.read_text(change.path).content
        except Exception as exc:
            before = f"(Could not read existing text for diff: {exc})\n"

        after = change.content or ""
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{change.path}",
            tofile=(f"b/{change.path}" if change.change_type.value != "delete" else "/dev/null"),
        )
        rendered = "".join(diff)
        print(rendered if rendered else "(No textual diff available.)")

    print("\nVERIFICATION COMMANDS")
    for command in plan.verification_commands:
        print("  $ " + " ".join(command))
    print("\nNo files have been changed yet.")


def _approve_plan(plan, workspace: Workspace) -> bool:
    """Require an explicit interactive confirmation after displaying the plan."""
    _display_plan(plan, workspace)
    try:
        answer = input("\nApply this exact plan? Type 'yes' to proceed: ")
    except (EOFError, KeyboardInterrupt):
        print("\nApproval not received. No changes applied.")
        return False
    return answer.strip().casefold() == "yes"



def _collect_manual_verification(criteria) -> dict[str, dict[str, str]]:
    """Collect explicit human PASS/FAIL evidence for manual ESpec criteria."""
    if not criteria:
        return {}
    print("\nMANUAL ACCEPTANCE VERIFICATION")
    print("=" * 72)
    print("For each criterion, enter PASS: followed by concrete evidence, FAIL: followed by the issue, or leave blank to keep it unverified.")
    evidence = {}
    for criterion in criteria:
        print(f"\n[{criterion.criterion_id}] {criterion.description}")
        try:
            answer = input("Evidence: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nManual verification interrupted; remaining criteria stay unverified.")
            break
        if not answer:
            continue
        decision, separator, detail = answer.partition(":")
        decision = decision.strip().upper()
        detail = detail.strip()
        if separator and decision in {"PASS", "FAIL"} and detail:
            evidence[criterion.criterion_id] = {"status": decision, "evidence": detail}
        else:
            print("  Not recorded. Use PASS: <specific evidence> or FAIL: <specific issue>.")
    return evidence

def _approve_retry(request: RetryRequest) -> bool:
    """Ask whether the agent may prepare another corrective plan."""
    print("\nIMPLEMENTATION ATTEMPT FAILED")
    print("=" * 72)
    print(f"Attempt: {request.attempt_number} of {request.max_attempts}")
    print(f"Failure phase: {request.phase}")
    print(request.summary)
    for error in request.errors:
        print(f"  - {error}")
    try:
        answer = input("\nAllow the agent to generate a corrective plan? Type 'yes' to retry: ")
    except (EOFError, KeyboardInterrupt):
        print("\nRetry approval not received.")
        return False
    return answer.strip().casefold() == "yes"


def _load_manual_evidence(path: str) -> dict[str, dict[str, str]]:
    """Read human-supplied manual verification evidence. Nothing is inferred."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"--manual-evidence could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("--manual-evidence must be a JSON object keyed by criterion ID")
    evidence: dict[str, dict[str, str]] = {}
    for criterion_id, item in data.items():
        if not isinstance(item, dict):
            raise ValueError(f"manual evidence for {criterion_id} must be an object")
        status = str(item.get("status", "")).strip().upper()
        detail = str(item.get("evidence", "")).strip()
        if status not in {"PASS", "FAIL"} or not detail:
            raise ValueError(
                f"manual evidence for {criterion_id} needs status PASS or FAIL and non-empty evidence"
            )
        evidence[str(criterion_id)] = {"status": status, "evidence": detail}
    return evidence


def _run_coding_task(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    workspace = _workspace(args.workspace)
    task = CodingTask(
        task_id=args.task_id or f"task-{uuid4().hex[:12]}",
        description=args.description,
        workspace=str(workspace.root),
        constraints=tuple(args.constraint),
        acceptance_criteria=tuple(args.acceptance_criteria),
    )

    journal_path = (
        Path(args.journal).expanduser()
        if args.journal
        else workspace.root / ".coding-agent" / "changes.jsonl"
    )
    if not journal_path.is_absolute():
        journal_path = workspace.root / journal_path

    manual_evidence = (
        _load_manual_evidence(args.manual_evidence) if args.manual_evidence else None
    )
    manual_handler = (
        (lambda criteria: {
            item.criterion_id: manual_evidence[item.criterion_id]
            for item in criteria
            if item.criterion_id in manual_evidence
        })
        if manual_evidence is not None
        else _collect_manual_verification
    )

    journal = ChangeJournal(journal_path)
    coordinator = ChangeCoordinator(workspace, journal=journal)
    runner = CommandRunner(workspace, timeout=args.timeout)
    client_options = (
        {} if args.llm_timeout is None else {"timeout": args.llm_timeout}
    )
    planning_client = OllamaClient(role="planning", **client_options)
    coding_client = OllamaClient(role="coding", **client_options)
    planner = CodingPlanner(client=coding_client)
    context_engine = ContextEngine(
        workspace,
        max_files=args.max_context_files,
        max_context_chars=args.max_context_chars,
    )
    orchestrator = TaskOrchestrator(
        planner=planner,
        coordinator=coordinator,
        command_runner=runner,
        verification_engine=VerificationEngine(),
        context_engine=context_engine,
        retry_policy=RetryPolicy(max_attempts=args.max_attempts),
        specification_client=planning_client,
        verification_gate={"spec": "specification"}.get(args.verify, args.verify),
    )

    print(f"Task: {task.task_id}")
    print(f"Workspace: {workspace.root}")
    print(
        "Building context: planning model will design the architecture and implementation plan; "
        "coding model will generate the implementation..."
    )
    result = orchestrator.run(
        task,
        approval_handler=lambda plan: _approve_plan(plan, workspace),
        retry_handler=_approve_retry,
        manual_verification_handler=manual_handler,
    )

    print(f"\nTASK STATUS: {result.status.value.upper()}")
    for path in result.changed_files:
        print(f"  Changed: {path}")
    for note in result.notes:
        print(f"  Note: {note}")
    for attempt in result.attempt_history:
        decision = f"; retry {attempt.retry_decision}" if attempt.retry_decision else ""
        print(
            f"  Attempt {attempt.attempt_number}: {attempt.phase} -> "
            f"{attempt.outcome}{decision}"
        )
    for error in result.errors:
        print(f"  ERROR: {error}")
    for command_result in result.command_results:
        command = " ".join(command_result.command)
        print(f"  Verification: {command} -> exit {command_result.exit_code}")
        if command_result.stdout.strip():
            print("  stdout:")
            print(command_result.stdout.rstrip())
        if command_result.stderr.strip():
            print("  stderr:")
            print(command_result.stderr.rstrip())
    print(f"Journal: {journal_path}")
    print(f"Task journal: {workspace.root / '.coding-agent' / 'tasks.jsonl'}")

    return 0 if result.status == TaskStatus.COMPLETED and result.succeeded else (
        1 if result.status == TaskStatus.FAILED else 2
    )


def _run(args: argparse.Namespace) -> int:
    if args.command == "run":
        return _run_coding_task(args)

    workspace, index = _index(args.workspace, args.max_files)

    if args.command == "index":
        result = {
            "workspace": str(workspace.root),
            "discovered_files": index.discovered_files,
            "indexed_files": index.indexed_files,
            "failed_files": index.failed_files,
            "languages": sorted({module.language for module in index.modules}),
            "modules": len(index.modules),
            "symbols": len(index.symbols),
            "relationships": len(index.relationships),
            "unresolved_relationships": len(index.unresolved_relationships),
            "parse_failures": [
                {"path": module.relative_path, "error": module.parse_error}
                for module in index.modules if module.parse_error
            ],
        }
        if args.json:
            _emit(result, as_json=True)
        else:
            print(f"Workspace: {result['workspace']}")
            print(f"Files: {index.indexed_files}/{index.discovered_files} indexed; {index.failed_files} failed")
            print(f"Modules: {len(index.modules)} | Symbols: {len(index.symbols)}")
            print(f"Relationships: {len(index.relationships)} ({len(index.unresolved_relationships)} unresolved/ambiguous)")
            print(f"Languages: {', '.join(result['languages']) or '(none)'}")
            for failure in result["parse_failures"]:
                print(f"  ERROR {failure['path']}: {failure['error']}")
        return 0 if index.failed_files == 0 else 1

    graph = index.build_knowledge_graph()

    if args.command == "graph":
        if args.json:
            _emit({"nodes": [_node_dict(node) for node in graph.nodes],
                   "edges": [_edge_dict(edge) for edge in graph.edges]}, as_json=True)
        else:
            _emit(graph.render(max_nodes=args.max_nodes, max_edges=args.max_edges))
        return 0

    if args.command == "search":
        matches = graph.find_nodes(args.query, kind=args.kind,
                                   path=args.path, language=args.language)
        matches = matches[:max(0, args.limit)]
        if args.json:
            _emit([_node_dict(node) for node in matches], as_json=True)
        else:
            if not matches:
                print("No matching nodes found.")
            for node in matches:
                location = f"{node.path}:{node.line}" if node.path and node.line else (node.path or "")
                print(f"{node.kind:9} {node.label}  {location}  [{node.id}]")
        return 0

    if args.command in {"relationships", "impact"}:
        node = _resolve_target(graph, args.target)
        if args.command == "impact":
            traversal = graph.impact_analysis(node.id, max_depth=args.depth, kinds=args.kinds)
        else:
            statuses = None if args.include_unresolved else ("resolved",)
            edges = []
            if args.direction in {"out", "both"}:
                edges.extend(graph.outgoing(node.id, kinds=args.kinds, statuses=statuses))
            if args.direction in {"in", "both"}:
                edges.extend(graph.incoming(node.id, kinds=args.kinds, statuses=statuses))
            edges = list(dict.fromkeys(edges))
            nodes_by_id = {item.id: item for item in graph.nodes}
            traversal_nodes = tuple(nodes_by_id[edge.target_id if edge.source_id == node.id else edge.source_id]
                                    for edge in edges)
            from coding_agent.knowledge_graph import GraphTraversal
            traversal = GraphTraversal(node.id, args.direction, traversal_nodes, tuple(edges))
        payload = {"start": _node_dict(node), "direction": traversal.direction,
                   "nodes": [_node_dict(item) for item in traversal.nodes],
                   "edges": [_edge_dict(item) for item in traversal.edges]}
        if args.json:
            _emit(payload, as_json=True)
        else:
            print(f"{args.command.upper()}: {node.label} [{node.id}]")
            if not traversal.edges:
                print("No matching relationships found.")
            for edge in traversal.edges:
                source = graph.get_node(edge.source_id)
                target_node = graph.get_node(edge.target_id)
                print(f"{source.label if source else edge.source_id} --{edge.kind}--> "
                      f"{target_node.label if target_node else edge.target_id} [{edge.status}]")
        return 0

    if args.command == "context":
        task = CodingTask(task_id=args.task_id, description=args.description,
                          workspace=str(workspace.root))
        engine = ContextEngine(workspace, max_files=args.max_context_files,
                               max_context_chars=args.max_context_chars)
        package = engine.build(task)
        if args.json:
            _emit({"task_id": package.task_id, "workspace": package.workspace,
                   "files": [{"path": item.relative_path, "size_bytes": item.size_bytes,
                              "relevance_score": item.relevance_score,
                              "truncated": item.truncated} for item in package.files],
                   "directory_tree": list(package.directory_tree),
                   "discovered_files": package.discovered_files,
                   "omitted_files": package.omitted_files,
                   "total_characters": package.total_characters,
                   "rendered_context": package.render()}, as_json=True)
        else:
            _emit(package.render())
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "max_files") and args.max_files < 1:
            raise ValueError("--max-files must be at least 1")
        if hasattr(args, "limit") and args.limit < 0:
            raise ValueError("--limit must not be negative")
        if hasattr(args, "depth") and args.depth < 1:
            raise ValueError("--depth must be at least 1")
        if hasattr(args, "max_nodes") and (args.max_nodes < 0 or args.max_edges < 0):
            raise ValueError("--max-nodes and --max-edges must not be negative")
        if hasattr(args, "timeout") and args.timeout <= 0:
            raise ValueError("--timeout must be greater than zero")
        if (
            hasattr(args, "llm_timeout")
            and args.llm_timeout is not None
            and args.llm_timeout <= 0
        ):
            raise ValueError("--llm-timeout must be greater than zero")
        if hasattr(args, "max_attempts") and not 1 <= args.max_attempts <= 10:
            raise ValueError("--max-attempts must be between 1 and 10")
        if hasattr(args, "max_context_files") and args.max_context_files < 1:
            raise ValueError("--max-context-files must be at least 1")
        if hasattr(args, "max_context_chars") and args.max_context_chars < 1:
            raise ValueError("--max-context-chars must be at least 1")
        return _run(args)
    except (OSError, ValueError, RuntimeError, WorkspaceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
