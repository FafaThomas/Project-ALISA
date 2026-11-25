"""Command line for the coding agent and any of its units.

    coding-agent units                                            print the unit tree
    coding-agent run <workspace> "<task>"                         the whole agent
    coding-agent run <workspace> "<task>" --unit security         one division
    coding-agent run <workspace> "<task>" --unit development/database_developer
    coding-agent team <workspace> "<task>" [--unit <division>]    preview team selection (no model)
    coding-agent serve --allow-root <dir> [--unit <path>] [--port N]
    coding-agent scaffold [--unit <path>] [--check] [--force]    create/check units' agent.py, api.py, cli.py

Every unit folder also has its own entry files, e.g.
    python divisions/development/python_developer/cli.py D:/work/app "<task>"
    python divisions/development/python_developer/api.py --allow-root D:/work --port 8801
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from .agent import CodingAgent
from .core.models import CodingRequest, InvocationContext, InvocationSource, RunReport
from .core.registry import default_tree
from .core.selection import TeamAssembler
from .core.workspace import Workspace


def _name(unit_id: str) -> str:
    unit = default_tree().by_id.get(unit_id)
    return unit.name if unit else unit_id


def _print_event(event: dict) -> None:
    kind = event.get("event")
    if kind == "run_started":
        print(f"[{event['level']}] {event['path'] or event['unit']}   context: {event['context_strategy']}")
    elif kind == "team":
        print(f"[team] {', '.join(event['team'])}")
        print(f"       {event.get('rationale', '')}")
    elif kind == "phase":
        print(f"\n== {event['phase']} ({', '.join(event['divisions'])}) ==")
    elif kind == "role_started":
        extra = f" ({event['work_item']})" if event.get("work_item") else ""
        extra += " as lead" if event.get("lead") else ""
        extra += " [remote]" if event.get("remote") else ""
        print(f"  -> [{event.get('division', '')}] {_name(event['role'])}{extra} ...", flush=True)
    elif kind == "team_routed":
        print(f"     {_name(event['team'])} routed to {', '.join(_name(m) for m in event['members'])}")
    elif kind == "role_finished":
        model = f"  ({event['model']}, {event['model_source']})" if event.get("model") else ""
        if event.get("error"):
            print(f"     ! {event['error']}{model}")
        elif event.get("files"):
            print(f"     wrote {', '.join(event['files'])}{model}")
        elif model:
            print(f"     done{model}")
    elif kind == "verification":
        state = "passed" if event["passed"] else f"{len(event['problems'])} problem(s)"
        print(f"  [files gate] {state}")
    elif kind == "test_env":
        if event.get("note"):
            print(f"  [test env] {event['note']}")
        if event.get("warning"):
            print(f"  [test env] ! {event['warning']}")
    elif kind == "test_run":
        print("  [test run]")
        for r in event["results"]:
            if r["skipped"]:
                status = f"skipped ({r['skipped']})"
            elif r.get("unavailable"):
                status = f"n/a ({r['unavailable']})"
            elif r["timed_out"]:
                status = "HUNG"
            elif "Traceback (most recent call last)" in r["stderr"]:
                status = "CRASHED"
            elif r.get("reported"):
                status = "ERRORS"
            else:
                status = f"ok (exit {r['exit_code']})"
            print(f"     {status:<14} {r.get('ran_as') or r['command']}")


def _summarize(report: RunReport) -> None:
    print(f"\nstatus: {report.status}   unit: {report.unit_level} {report.unit_id}   "
          f"context: {report.context_strategy}   run: {report.run_id}")
    for change in report.changes:
        print(f"  {change.action:<6} {change.path}  ({_name(change.author_role)})")
    if report.verification and report.verification.problems:
        print("files gate problems:")
        for p in report.verification.problems:
            print(f"  - {p['path']} [{p['check']}] {p['detail']}")
    if report.advisory_findings:
        print("advisory review findings:")
        for f in report.advisory_findings:
            print(f"  - [{f.severity}] {f.file or '-'}: {f.issue} ({_name(f.role)})")
    for warning in report.warnings:
        print(f"warning: {warning}")
    if report.error:
        print(f"error: {report.error}")
    print(f"artifacts: {report.artifacts_dir}")


def _unit_ref(args: argparse.Namespace) -> str | None:
    refs = [r for r in (args.unit, getattr(args, "division", None), getattr(args, "specialist", None)) if r]
    if len(refs) > 1:
        raise ValueError("use only one of --unit, --division, --specialist")
    return refs[0] if refs else None


def cmd_run(args: argparse.Namespace) -> int:
    request = CodingRequest(
        task=args.task, workspace=args.workspace, constraints=args.constraint,
        acceptance_criteria=args.acceptance_criteria, mode="propose", requested_roles=args.role,
        max_team_size=args.max_team, fix_review_findings=args.fix_review,
    )
    invocation = InvocationContext(source=InvocationSource.CLI, caller_id="cli")
    agent = CodingAgent(use_director_llm=not args.no_director, max_repairs=args.max_repairs)
    unit = agent.unit(_unit_ref(args))
    report = unit.run(request, invocation, emit=None if args.json else _print_event)
    if report.status == "proposed" and not args.propose_only:
        if args.json:
            print("refusing to apply in --json mode without an interactive approval", file=sys.stderr)
        else:
            _summarize(report)
            answer = input("\nWrite these changes to the workspace? Type 'yes' to approve: ").strip()
            if answer == "yes":
                report = agent.apply_proposal(report, invocation)
            else:
                print("Not applied. Proposed files are kept under the run's artifacts folder.")
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        _summarize(report)
    return 0 if report.status in ("completed", "proposed") else 1


def cmd_team(args: argparse.Namespace) -> int:
    request = CodingRequest(task=args.task, workspace=args.workspace, requested_roles=args.role,
                            max_team_size=args.max_team)
    files = Workspace(args.workspace).list_files()
    tree = default_tree()
    assembler = TeamAssembler(None, tree=tree, use_director_llm=False)
    ref = _unit_ref(args)
    if ref:
        unit = tree.get(ref)
        if unit.level != "division":
            raise ValueError("team previews work for the agent or a division")
        team = assembler.for_division(request, files, unit)
    else:
        team = assembler.heuristic(request, files)
    for role in team.roles:
        print(f"{role.phase:<8} {role.division:<14} {role.name:<34} {role.kind:<11} tier={role.tier}")
    for role in team.standby:
        print(f"{'standby':<8} {role.division:<14} {role.name:<34} {role.kind:<11} tier={role.tier}")
    print(f"\n{team.rationale}")
    return 0


def _print_unit(node: dict[str, Any], indent: int) -> None:
    pad = "  " * indent
    extras = []
    if node.get("lead"):
        extras.append(f"lead: {_name(node['lead'])}")
    if node.get("model"):
        extras.append(f"model: {node['model']}")
    if node.get("endpoint"):
        extras.append(f"endpoint: {node['endpoint']}")
    if node.get("has_tools"):
        extras.append("+tools")
    if node.get("has_requirements"):
        extras.append("+requirements")
    if node["level"] == "specialist":
        team = " (team)" if node.get("is_team") else ""
        print(f"{pad}{node['id']:<{40 - len(pad)}} {node['kind']:<11}{team} {node['responsibility']}"
              + (f"  [{'; '.join(extras)}]" if extras else ""))
    else:
        print(f"{pad}{node['name']} ({node['level']}: {node['path'] or node['id']})"
              + (f"  [{'; '.join(extras)}]" if extras else ""))
    for child in node["units"]:
        _print_unit(child, indent + 1)


def cmd_units(args: argparse.Namespace) -> int:
    tree = default_tree()
    node = tree.describe(tree.get(args.unit) if args.unit else tree.root)
    if args.json:
        print(json.dumps(node, indent=2))
    else:
        _print_unit(node, 0)
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    from .core.scaffold import check, scaffold

    tree = default_tree()
    if args.check:
        problems = check(tree)
        for problem in problems:
            print(problem)
        print(f"{len(problems)} problem(s) across {len(tree.units)} units")
        return 1 if problems else 0
    units = tree.subtree(tree.get(args.unit)) if args.unit else None
    written = scaffold(tree, units, force=args.force)
    for path in written:
        print(f"wrote {path}")
    print(f"{len(written)} file(s) written")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .api import CodingAgentService, serve

    service = CodingAgentService(CodingAgent(), allowed_roots=args.allow_root, unit=args.unit)
    serve(service, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coding-agent", description="ALISA Forge coding agent and its units")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the agent or one unit, approve, then write")
    run.add_argument("workspace")
    run.add_argument("task")
    run.add_argument("--unit", help="unit path or id to run instead of the whole agent")
    run.add_argument("--division", help=argparse.SUPPRESS)
    run.add_argument("--specialist", help=argparse.SUPPRESS)
    run.add_argument("--constraint", action="append", default=[])
    run.add_argument("--acceptance-criteria", action="append", default=[])
    run.add_argument("--role", action="append", default=[], help="force a specialist onto the team")
    run.add_argument("--max-team", type=int, default=None)
    run.add_argument("--max-repairs", type=int, default=2)
    run.add_argument("--fix-review", action="store_true", help="send high/critical review findings back once")
    run.add_argument("--no-director", action="store_true", help="skip the Director model; use heuristic team")
    run.add_argument("--propose-only", action="store_true", help="never write; keep proposals in the run folder")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    team = sub.add_parser("team", help="preview the heuristic team for a task (no model)")
    team.add_argument("workspace")
    team.add_argument("task")
    team.add_argument("--unit", help="a division to preview instead of the agent")
    team.add_argument("--division", help=argparse.SUPPRESS)
    team.add_argument("--role", action="append", default=[])
    team.add_argument("--max-team", type=int, default=None)
    team.set_defaults(func=cmd_team)

    units = sub.add_parser("units", help="print the unit tree with models, endpoints and tools")
    units.add_argument("--unit", help="print only this unit and what is under it")
    units.add_argument("--json", action="store_true")
    units.set_defaults(func=cmd_units)

    scaffold = sub.add_parser("scaffold", help="create missing agent.py/api.py/cli.py in unit folders")
    scaffold.add_argument("--unit", help="only this unit and the units under it")
    scaffold.add_argument("--check", action="store_true", help="report missing or invalid entry files")
    scaffold.add_argument("--force", action="store_true", help="overwrite existing entry files")
    scaffold.set_defaults(func=cmd_scaffold)

    serve = sub.add_parser("serve", help="serve the agent, or one unit, over HTTP")
    serve.add_argument("--allow-root", action="append", required=True, help="workspace root the service may use")
    serve.add_argument("--unit", help="serve only this unit (path or id)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except (ValueError, KeyError) as exc:
        print(f"error: {str(exc).strip(chr(39))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
