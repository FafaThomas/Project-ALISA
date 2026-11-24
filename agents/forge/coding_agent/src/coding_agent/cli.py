"""Command line for the coding agent (ALISA internal use and local testing).

    coding-agent run <workspace> "<task>"                      the whole agent
    coding-agent run <workspace> "<task>" --division security  one division
    coding-agent run <workspace> "<task>" --specialist dependency_auditor
    coding-agent team <workspace> "<task>" [--division id]     preview team selection (no LLM)
    coding-agent units                                         print the hierarchy
    coding-agent serve --allow-root <dir>                      start the HTTP API
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .agent import CodingAgent
from .core.models import CodingRequest, InvocationContext, InvocationSource, RunReport
from .core.registry import DIVISIONS, ROLES
from .core.selection import TeamAssembler
from .core.workspace import Workspace


def _print_event(event: dict) -> None:
    kind = event.get("event")
    if kind == "team":
        print(f"[team] {', '.join(event['team'])}")
        print(f"       {event.get('rationale', '')}")
    elif kind == "run_started":
        print(f"[{event['level']}] {event['unit']}   context: {event['context_strategy']}")
    elif kind == "phase":
        print(f"\n== {event['phase']} ({', '.join(event['divisions'])}) ==")
    elif kind == "role_started":
        role = ROLES.get(event["role"])
        extra = f" ({event['work_item']})" if event.get("work_item") else ""
        extra += " as division lead" if event.get("lead") else ""
        print(f"  -> [{event.get('division', '')}] {role.name if role else event['role']}{extra} ...", flush=True)
    elif kind == "role_finished":
        if event.get("error"):
            print(f"     ! {event['error']}")
        elif event.get("files"):
            print(f"     wrote {', '.join(event['files'])}")
    elif kind == "verification":
        state = "passed" if event["passed"] else f"{len(event['problems'])} problem(s)"
        print(f"  [files gate] {state}")


def _summarize(report: RunReport) -> None:
    print(f"\nstatus: {report.status}   unit: {report.unit_level} {report.unit_id}   "
          f"context: {report.context_strategy}   run: {report.run_id}")
    for change in report.changes:
        print(f"  {change.action:<6} {change.path}  ({ROLES[change.author_role].name if change.author_role in ROLES else change.author_role})")
    if report.verification and report.verification.problems:
        print("files gate problems:")
        for p in report.verification.problems:
            print(f"  - {p['path']} [{p['check']}] {p['detail']}")
    if report.advisory_findings:
        print("advisory review findings:")
        for f in report.advisory_findings:
            print(f"  - [{f.severity}] {f.file or '-'}: {f.issue} ({ROLES[f.role].name})")
    if report.error:
        print(f"error: {report.error}")
    print(f"artifacts: {report.artifacts_dir}")


def cmd_run(args: argparse.Namespace) -> int:
    request = CodingRequest(
        task=args.task, workspace=args.workspace, constraints=args.constraint,
        acceptance_criteria=args.acceptance_criteria, mode="propose", requested_roles=args.role,
        max_team_size=args.max_team, fix_review_findings=args.fix_review,
    )
    invocation = InvocationContext(source=InvocationSource.CLI, caller_id="cli")
    agent = CodingAgent(use_director_llm=not args.no_director, max_repairs=args.max_repairs)
    if args.division and args.specialist:
        raise ValueError("use either --division or --specialist, not both")
    unit = (agent.division(args.division) if args.division
            else agent.specialist(args.specialist) if args.specialist else agent)
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
    assembler = TeamAssembler(None, use_director_llm=False)
    if args.division:
        if args.division not in DIVISIONS:
            raise ValueError(f"unknown division {args.division!r}")
        team = assembler.for_division(request, files, DIVISIONS[args.division])
    else:
        team = assembler.heuristic(request, files)
    for role in team.roles:
        print(f"{role.phase:<8} {role.division:<14} {role.name:<34} {role.kind:<11} tier={role.tier}")
    for role in team.standby:
        print(f"{'standby':<8} {role.division:<14} {role.name:<34} {role.kind:<11} tier={role.tier}")
    print(f"\n{team.rationale}")
    return 0


def cmd_units(args: argparse.Namespace) -> int:
    tree = CodingAgent.describe()
    print(f"{tree['name']} (agent)  lead: {ROLES[tree['lead']].name}")
    for division in tree["divisions"]:
        print(f"\n  {division['name']} (division: {division['id']})  lead: {ROLES[division['lead']].name}")
        for s in division["specialists"]:
            tools = " +tools" if s["has_tools"] else ""
            print(f"    {s['id']:<36} {s['kind']:<11} {s['responsibility']}{tools}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .api import CodingAgentService, serve

    service = CodingAgentService(CodingAgent(), allowed_roots=args.allow_root)
    serve(service, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coding-agent", description="ALISA Forge coding team")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="generate changes with the team, approve, then write")
    run.add_argument("workspace")
    run.add_argument("task")
    run.add_argument("--constraint", action="append", default=[])
    run.add_argument("--acceptance-criteria", action="append", default=[])
    run.add_argument("--division", help="run one division instead of the whole agent")
    run.add_argument("--specialist", help="run one specialist instead of the whole agent")
    run.add_argument("--role", action="append", default=[], help="force a specialist onto the team")
    run.add_argument("--max-team", type=int, default=None)
    run.add_argument("--max-repairs", type=int, default=2)
    run.add_argument("--fix-review", action="store_true", help="send high/critical review findings back once")
    run.add_argument("--no-director", action="store_true", help="skip the Director model; use heuristic team")
    run.add_argument("--propose-only", action="store_true", help="never write; keep proposals in the run folder")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    team = sub.add_parser("team", help="preview the heuristic team for a task (no LLM)")
    team.add_argument("workspace")
    team.add_argument("task")
    team.add_argument("--division")
    team.add_argument("--role", action="append", default=[])
    team.add_argument("--max-team", type=int, default=None)
    team.set_defaults(func=cmd_team)

    units = sub.add_parser("units", help="print the agent > division > specialist hierarchy")
    units.set_defaults(func=cmd_units)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--allow-root", action="append", required=True, help="workspace root the API may write under")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
