"""Running the unit tree: Agent > Division > Specialist > (sub-specialists ...).

Every unit has the same entry point,
``run(request, invocation, emit=None) -> RunReport``, so a caller can address
the whole coding agent, one division, one specialist or one sub-specialist.

Rules that hold at every level:

- **Requests go down, reports come up.** A unit hands its members the request
  plus everything already on the shared blackboard, and collects their output.
- **Only the unit you called writes to disk.** That unit opens a ``Session``,
  runs the files gate and repairs, then applies changes (or keeps them as a
  proposal). Units below it only add to the session's proposed change set.
- **Context is inherited.** The session's context provider comes from the
  caller's invocation: Forge RAG only when the Forge called, Markdown otherwise.
- **Leads split work only when there is work to split.** A division lead is
  used for the build phase when the division has two or more builders
  (Development's Tech Lead whenever Development builds). A specialist called on
  its own works alone.

How a member does its work depends on its folder:

- a plain specialist runs in-process (``Specialist``);
- a team specialist (it has sub-specialists) routes the work to the members
  whose triggers match, splits it when several match, and does the work itself
  when none match (``TeamExecutor``);
- a unit whose ``unit.md`` declares an ``endpoint`` is called over HTTP on its
  own service (``RemoteExecutor``). The unit that was called always runs
  locally, so a service never calls itself.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import autofix, projectdocs, testrun
from .blackboard import Blackboard
from .context import build_context_provider
from .context.base import ContextProvider
from .llm import ModelRouter
from .models import (
    CodingRequest, FileChange, Finding, InvocationContext, InvocationSource, RoleOutput, RunReport,
    VerificationResult, WorkItem, file_change_from_dict, request_to_dict, role_output_from_dict,
    work_item_from_dict,
)
from .projectfacts import ProjectView
from .registry import PHASES, RoleSpec, Tree, UnitSpec
from .selection import division_needs_lead, score_units
from .specialist import Specialist
from .verification import SOFT_CHECKS, self_check_problems, verify_files
from .workspace import ChangeSet, Workspace, WorkspaceError

LOGGER = logging.getLogger(__name__)
Level = Literal["faction", "agent", "division", "specialist"]
Emit = Callable[[dict], None]
REPAIRER = "debugging_specialist"
BUILDER_KINDS = ("builder", "repairer")


class Executor(Protocol):
    """What a member unit can be asked to do. Specialist, TeamExecutor and RemoteExecutor all fit."""

    def consult(self, request: CodingRequest, board: Blackboard) -> RoleOutput: ...
    def coordinate(self, request: CodingRequest, board: Blackboard, builders: list[RoleSpec],
                   changes: ChangeSet, elsewhere: list[RoleSpec] | None = None) -> RoleOutput: ...
    def build(self, request: CodingRequest, board: Blackboard, item: WorkItem, all_items: list[WorkItem],
              changes: ChangeSet) -> RoleOutput: ...
    def repair(self, request: CodingRequest, problems: list[dict[str, str]], changes: ChangeSet,
               board: Blackboard) -> RoleOutput: ...
    def review(self, request: CodingRequest, board: Blackboard, subject: list[tuple[str, str, str]], *,
               label: str = ...) -> RoleOutput: ...


def default_target_environment() -> str:
    """CODING_AGENT_TARGET_ENVIRONMENT, else the OS and Python of the machine running the agent."""
    from .env import env_value, load_local_env

    load_local_env()
    configured = env_value("CODING_AGENT_TARGET_ENVIRONMENT")
    if configured:
        return configured
    system = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(platform.system(), platform.system())
    return f"{system} {platform.release()}, Python {sys.version_info.major}.{sys.version_info.minor}"


# ------------------------------------------------------------------- session
@dataclass
class Session:
    """State of one run, owned by the unit that was called."""

    request: CodingRequest
    invocation: InvocationContext
    router: ModelRouter
    tree: Tree
    workspace: Workspace
    provider: ContextProvider
    emit: Emit
    run_id: str
    run_dir: Path
    level: Level
    unit_id: str
    local_paths: set[str] = field(default_factory=set)
    changes: ChangeSet = field(init=False)
    board: Blackboard = field(init=False)
    outputs: list[RoleOutput] = field(default_factory=list)
    work_items: list[WorkItem] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    builders_ran: bool = False
    verification: VerificationResult | None = None
    test_commands: list[dict[str, str]] | None = None
    test_results: list = field(default_factory=list)
    test_warnings: list[str] = field(default_factory=list)
    team_groups: dict[str, list[RoleSpec]] = field(default_factory=dict)
    test_env_shown: tuple[str, str] = ("", "")
    tool_notes: list[str] = field(default_factory=list)       # fixes the tools made (autofix.py)
    test_notes: list[str] = field(default_factory=list)       # why the test run did not (fully) run
    import_notes: list[str] = field(default_factory=list)     # modules failing to import in code the run left alone
    plan_failures: list[str] = field(default_factory=list)    # leads that could not produce a plan
    migrations: set[str] = field(default_factory=set)         # migration files generated this run

    def __post_init__(self) -> None:
        self.changes = ChangeSet(self.workspace)
        self.board = Blackboard(tree=self.tree)

    @classmethod
    def open(
        cls, request: CodingRequest, invocation: InvocationContext, router: ModelRouter, tree: Tree, *,
        unit: UnitSpec, emit: Emit | None = None, run_id: str | None = None,
    ) -> "Session":
        workspace = Workspace(request.workspace)
        if not request.target_environment:
            request.target_environment = default_target_environment()
        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        return cls(
            request=request, invocation=invocation, router=router, tree=tree, workspace=workspace,
            provider=build_context_provider(workspace, invocation), emit=emit or (lambda event: None),
            run_id=run_id, run_dir=workspace.agent_dir / "runs" / run_id, level=unit.level, unit_id=unit.id,
            local_paths={unit.path},
        )

    def new_report(self) -> RunReport:
        return RunReport(
            run_id=self.run_id, status="failed", task=self.request.task, workspace=str(self.workspace.root),
            invocation_source=self.invocation.source.value, context_strategy=self.provider.strategy,
            team=[], unit_level=self.level, unit_id=self.unit_id, artifacts_dir=str(self.run_dir),
            constraints=list(self.request.constraints), acceptance_criteria=list(self.request.acceptance_criteria),
        )

    def executor(self, unit: UnitSpec) -> Executor:
        if unit.endpoint and unit.path not in self.local_paths:
            return RemoteExecutor(unit, self)
        if unit.is_team:
            return TeamExecutor(unit, self)
        return self.specialist(unit)

    def specialist(self, unit: UnitSpec) -> Specialist:
        return Specialist(unit, self.tree, self.router, self.provider, workspace_root=self.workspace.root)

    def record(self, output: RoleOutput, division: str) -> None:
        self.outputs.append(output)
        self.board.post(output)
        self.emit({
            "event": "role_finished", "division": division, "role": output.role, "phase": output.phase,
            "summary": output.summary, "error": output.error, "files": [c.path for c in output.changes],
            "model": output.model, "model_source": output.model_source,
        })

    def note_test(self, note: str) -> None:
        if note not in self.test_notes:
            self.test_notes.append(note)
            self.emit({"event": "test_note", "note": note})

    def add_tool_changes(self, files: list[tuple[str, str, str]], phase: str = "tools") -> list[str]:
        """Record fixes made without a model: (path, content, reason). Returns the paths that changed."""
        output = RoleOutput(role=autofix.TOOL_ROLE, phase=phase, summary="")
        reasons = []
        for path, content, reason in files:
            if self.changes.current(path) == content:
                continue
            try:
                change = self.changes.add(FileChange(path, "modify", content, author_role=autofix.TOOL_ROLE,
                                                     work_item_id=autofix.TOOL_ITEM))
            except WorkspaceError as exc:
                output.concerns.append(f"rejected file: {exc}")
                continue
            output.changes.append(change)
            reasons.append({"path": change.path, "action": change.action, "reason": reason})
            self.tool_notes.append(f"{change.path}: {reason}")
        if reasons:
            output.summary = "Fixed without a model: " + "; ".join(f"{r['path']} ({r['reason']})" for r in reasons)
            self.outputs.append(output)
            self.board.post(output)
            self.emit({"event": "tool_fix", "phase": phase, "fixes": reasons})
        return [r["path"] for r in reasons]


def apply_tool_fixes(session: Session, paths: list[str] | None = None) -> list[str]:
    """The team's tools fix what has one right answer (autofix.py), before any model is asked.

    ``paths``: only add missing imports to these files (a builder's own); dependency lists, Django
    boilerplate and Dockerfile fixes are made for every file the run changed.
    """
    view = ProjectView.of(session.changes)
    changed = set(session.changes.paths())
    if not changed:
        return []
    if paths is not None:
        fixes = autofix.import_fixes(view, [p for p in paths if p.endswith(".py")])
    else:
        fixes = autofix.deterministic_fixes(view, changed)
    return session.add_tool_changes([(f.path, f.content, f.reason) for f in fixes])


# --------------------------------------------------------------------- teams
def merge_outputs(unit: UnitSpec, phase: str, outputs: list[RoleOutput]) -> RoleOutput:
    merged = RoleOutput(role=unit.id, phase=phase, summary="; ".join(f"{o.role}: {o.summary}" for o in outputs))
    for output in outputs:
        merged.decisions += output.decisions
        merged.concerns += output.concerns
        merged.work_items += output.work_items
        merged.changes += output.changes
        merged.findings += output.findings
    merged.error = "; ".join(f"{o.role}: {o.error}" for o in outputs if o.error)
    merged.model = ", ".join(dict.fromkeys(o.model for o in outputs if o.model))
    merged.model_source = ", ".join(dict.fromkeys(o.model_source for o in outputs if o.model_source))
    return merged


class TeamExecutor:
    """A specialist that has sub-specialists.

    Work goes to the members whose triggers match it. One match: that member
    does it. Several: the team specialist splits the work between them, acting
    as their lead. None: the team specialist (the generalist) does it itself.
    """

    def __init__(self, unit: UnitSpec, session: Session) -> None:
        self.unit = unit
        self.session = session
        self.local = session.specialist(unit)

    def _route(self, *texts: str, kinds: tuple[str, ...] | None = None) -> list[UnitSpec]:
        members = [m for m in self.session.tree.children(self.unit) if kinds is None or m.kind in kinds]
        for text in texts:
            scores = score_units(text, members)
            if scores:
                order = {m.id: i for i, m in enumerate(members)}
                return sorted((m for m in members if m.id in scores), key=lambda m: (-scores[m.id], order[m.id]))
        return []

    def _note(self, matched: list[UnitSpec]) -> None:
        self.session.emit({"event": "team_routed", "team": self.unit.id, "members": [m.id for m in matched]})

    def consult(self, request: CodingRequest, board: Blackboard) -> RoleOutput:
        matched = self._route(request.task)
        if not matched:
            return self.local.consult(request, board)
        self._note(matched)
        outputs = [self.session.executor(m).consult(request, board) for m in matched]
        return outputs[0] if len(outputs) == 1 else merge_outputs(self.unit, self.unit.phase, outputs)

    def coordinate(self, request: CodingRequest, board: Blackboard, builders: list[RoleSpec],
                   changes: ChangeSet, elsewhere: list[RoleSpec] | None = None) -> RoleOutput:
        return self.local.coordinate(request, board, builders, changes, elsewhere)

    def build(self, request: CodingRequest, board: Blackboard, item: WorkItem, all_items: list[WorkItem],
              changes: ChangeSet) -> RoleOutput:
        item_text = f"{item.title}\n{item.description}\n{' '.join(item.files)}"
        matched = self._route(item_text, request.task, kinds=BUILDER_KINDS)
        if not matched:
            return self.local.build(request, board, item, all_items, changes)
        self._note(matched)
        if len(matched) == 1:
            return self.session.executor(matched[0]).build(request, board, item, all_items, changes)

        plan = self.local.coordinate(request, board, matched, changes)
        owners = {m.id for m in matched}
        sub_items = [w for w in plan.work_items if w.owner_role in owners]
        if not sub_items:
            sub_items = [WorkItem(f"{item.id}.1", item.title, matched[0].id, item.description, item.files)]
        for sub in sub_items:
            sub.id = f"{item.id}.{sub.id}"
        outputs = [self.session.executor(self.session.tree.by_id[sub.owner_role]).build(
            request, board, sub, sub_items, changes) for sub in sub_items]
        merged = merge_outputs(self.unit, "build", outputs)
        merged.concerns = plan.concerns + merged.concerns
        return merged

    def repair(self, request: CodingRequest, problems: list[dict[str, str]], changes: ChangeSet,
               board: Blackboard) -> RoleOutput:
        text = "\n".join(f"{p['path']} {p['detail']}" for p in problems)
        matched = self._route(text, request.task, kinds=BUILDER_KINDS)
        if len(matched) == 1:
            self._note(matched)
            return self.session.executor(matched[0]).repair(request, problems, changes, board)
        return self.local.repair(request, problems, changes, board)

    def review(self, request: CodingRequest, board: Blackboard, subject: list[tuple[str, str, str]], *,
               label: str = "Proposed changes") -> RoleOutput:
        matched = self._route(request.task, " ".join(path for _, path, _ in subject), kinds=("reviewer",))
        if not matched:
            return self.local.review(request, board, subject, label=label)
        self._note(matched)
        outputs = [self.session.executor(m).review(request, board, subject, label=label) for m in matched]
        return outputs[0] if len(outputs) == 1 else merge_outputs(self.unit, "review", outputs)


# -------------------------------------------------------------------- remote
REMOTE_TIMEOUT_SECONDS = 3600.0


class RemoteExecutor:
    """A unit running as its own service. Each call ships the run state it needs."""

    def __init__(self, unit: UnitSpec, session: Session) -> None:
        self.unit = unit
        self.session = session

    def _invoke(self, action: str, phase: str, args: dict[str, Any], board: Blackboard,
                changes: ChangeSet) -> RoleOutput:
        payload = {
            "unit": self.unit.path, "action": action, "run_id": self.session.run_id,
            "invocation_source": self.session.invocation.source.value,
            "request": request_to_dict(self.session.request),
            "board": [asdict(o) for o in board.entries],
            "changes": [asdict(c) for c in changes.changes()],
            "args": args,
        }
        headers = {"Content-Type": "application/json"}
        token = os.getenv("CODING_AGENT_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self.unit.endpoint.rstrip('/')}/invoke"  # type: ignore[union-attr]
        timeout = float(os.getenv("CODING_AGENT_REMOTE_TIMEOUT", REMOTE_TIMEOUT_SECONDS))
        try:
            request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            output = role_output_from_dict(body["output"])
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            return RoleOutput(role=self.unit.id, phase=phase, summary="",
                              error=f"remote unit {url} returned HTTP {exc.code}: {detail}")
        except (URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            return RoleOutput(role=self.unit.id, phase=phase, summary="", error=f"remote unit {url} failed: {exc}")

        accepted = []
        for change in output.changes:  # re-validate everything a remote unit proposes
            try:
                accepted.append(changes.add(change))
            except WorkspaceError as exc:
                output.concerns.append(f"rejected file from remote unit: {exc}")
        output.changes = accepted
        output.model_source = f"{output.model_source} via {self.unit.endpoint}".strip()
        return output

    def consult(self, request, board):
        return self._invoke("consult", self.unit.phase, {}, board, self.session.changes)

    def coordinate(self, request, board, builders, changes, elsewhere=None):
        return self._invoke("coordinate", "build", {"builders": [b.path for b in builders],
                                                    "elsewhere": [r.path for r in elsewhere or []]}, board, changes)

    def build(self, request, board, item, all_items, changes):
        return self._invoke("build", "build", {"item": asdict(item), "all_items": [asdict(w) for w in all_items]},
                            board, changes)

    def repair(self, request, problems, changes, board):
        return self._invoke("repair", "repair", {"problems": problems}, board, changes)

    def review(self, request, board, subject, *, label="Proposed changes"):
        return self._invoke("review", "review", {"subject": [list(s) for s in subject], "label": label},
                            board, self.session.changes)


def handle_invoke(tree: Tree, router: ModelRouter, payload: dict[str, Any]) -> dict[str, Any]:
    """Serve one ``RemoteExecutor`` call on the unit's own service."""
    unit = tree.get(str(payload.get("unit", "")))
    request = CodingRequest.from_payload(payload.get("request") or {})
    try:
        source = InvocationSource(payload.get("invocation_source", "api"))
    except ValueError as exc:
        raise ValueError("unknown invocation_source") from exc
    # The Forge's retriever object cannot cross the wire: remote units use Markdown context.
    invocation = InvocationContext(source=source, caller_id="parent-unit")
    session = Session.open(request, invocation, router, tree, unit=unit, run_id=payload.get("run_id"))
    for entry in payload.get("board") or []:
        session.board.post(role_output_from_dict(entry))
    for change in payload.get("changes") or []:
        session.changes.add(file_change_from_dict(change))

    executor = session.executor(unit)
    args = payload.get("args") or {}
    action = payload.get("action")
    if action == "consult":
        output = executor.consult(request, session.board)
    elif action == "coordinate":
        builders = [tree.get(p) for p in args.get("builders") or []]
        elsewhere = [tree.get(p) for p in args.get("elsewhere") or []]
        output = executor.coordinate(request, session.board, builders, session.changes, elsewhere)
    elif action == "build":
        items = [work_item_from_dict(w) for w in args.get("all_items") or []]
        item = work_item_from_dict(args.get("item") or {})
        output = executor.build(request, session.board, item, items or [item], session.changes)
    elif action == "repair":
        output = executor.repair(request, list(args.get("problems") or []), session.changes, session.board)
    elif action == "review":
        subject = [tuple(s) for s in args.get("subject") or []]
        output = executor.review(request, session.board, subject, label=str(args.get("label", "Proposed changes")))
    else:
        raise ValueError(f"unknown action {action!r}")
    return {"output": asdict(output)}


# ------------------------------------------------------------------ division
class DivisionRunner:
    """Runs one division's members for one phase."""

    def __init__(self, division: UnitSpec) -> None:
        self.division = division

    def _started(self, session: Session, role: RoleSpec, phase: str, **extra: Any) -> None:
        session.emit({"event": "role_started", "division": self.division.id, "role": role.id, "phase": phase,
                      "remote": bool(role.endpoint and role.path not in session.local_paths), **extra})

    def run_phase(self, session: Session, members: list[RoleSpec], phase: str) -> None:
        if phase == "build":
            return self._build(session, members)
        if phase == "review":
            return self._review(session, members)
        builders = [m for m in members if m.kind in BUILDER_KINDS]
        for role in members:
            consults = role.phase == phase and role.kind in ("analyst", "planner", "designer")
            # A Tech Lead with nobody to assign work to reports its plan instead.
            lone_coordinator = role.kind == "coordinator" and phase == "plan" and not builders
            if role.id == session.tree.root.lead or not (consults or lone_coordinator):
                continue
            self._started(session, role, phase)
            session.record(session.executor(role).consult(session.request, session.board), self.division.id)

    def _build(self, session: Session, members: list[RoleSpec]) -> None:
        builders = [m for m in members if m.kind in BUILDER_KINDS]
        if not builders:
            return
        session.builders_ran = True
        items: list[WorkItem] = []
        lead_id = self.division.lead
        # A specialist called on its own works alone; it does not pull in its lead.
        if session.level != "specialist" and lead_id and division_needs_lead(self.division, builders):
            lead = session.tree.by_id[lead_id]
            self._started(session, lead, "build", lead=True)
            # Builders from other divisions on this run: their work is left to them (Docker to DevOps, ...).
            elsewhere = [r for division_id, members in session.team_groups.items() if division_id != self.division.id
                         for r in members if r.kind == "builder"]
            plan = session.executor(lead).coordinate(session.request, session.board, builders, session.changes,
                                                     elsewhere)
            if plan.error and not plan.work_items:
                # One more try: a model can fail to produce valid JSON once and succeed the next time.
                session.record(plan, self.division.id)
                self._started(session, lead, "build", lead=True, retry=True)
                plan = session.executor(lead).coordinate(session.request, session.board, builders, session.changes,
                                                         elsewhere)
            items = [i for i in plan.work_items if i.owner_role in {b.id for b in builders}]
            if not items and plan.error:
                # Improvising without a plan put the wrong specialist on the wrong files in real runs: stop instead.
                session.plan_failures.append(f"{lead.name} ({self.division.name}): {plan.error[:200]}")
                session.record(plan, self.division.id)
                return
            if not items and elsewhere:
                # An empty plan while other divisions build is an answer: "this is their part, not ours".
                plan.concerns.append("no work items: the lead left this request to the other divisions on this run")
                session.record(plan, self.division.id)
                return
            if not items:
                plan.concerns.append("no usable work items; each specialist takes its part directly")
            session.record(plan, self.division.id)
        if not items:
            owned = sorted(session.changes.owned())
            taken_note = (f" These files already belong to other work items and you may not change them: "
                          f"{', '.join(owned[:40])}." if owned else "")
            items = [
                WorkItem(f"{self.division.id}-{b.id}", f"{b.name}: {self.division.name} part of the request",
                         b.id, f"Deliver the part of the request your role is responsible for ({b.responsibility})."
                               f"{taken_note} If no part of the request falls under your responsibility, return an "
                               "empty files list: that is a valid answer.")
                for b in builders
            ]
        taken = {w.id for w in session.work_items}
        for item in items:
            if item.id in taken:
                item.id = f"{self.division.id}-{item.id}"
            taken.add(item.id)
            for path in item.files:
                session.changes.claim(path, item.id, item.owner_role)
        session.work_items.extend(items)
        for item in items:
            role = session.tree.by_id[item.owner_role]
            self._started(session, role, "build", work_item=item.id)
            executor = session.executor(role)
            output = executor.build(session.request, session.board, item, items, session.changes)
            session.record(output, self.division.id)
            if isinstance(executor, Specialist) and output.changes and not output.error:
                self._self_check(session, executor, role, item, [c.path for c in output.changes])

    def _self_check(self, session: Session, executor: Specialist, role: RoleSpec, item: WorkItem,
                    paths: list[str]) -> None:
        """Check a builder's files as soon as they are written; the builder fixes its own mistakes first."""
        apply_tool_fixes(session, paths)  # missing imports with one right answer: no model needed
        rounds = self_check_rounds()
        for _ in range(rounds):
            problems = self_check_problems(session.changes, paths)
            if not problems:
                return
            session.emit({"event": "self_check", "role": role.id, "work_item": item.id, "problems": problems})
            self._started(session, role, "self-check", work_item=item.id)
            fixed = executor.fix_own(session.request, session.board, item, problems, session.changes)
            session.record(fixed, self.division.id)
            if fixed.error or not fixed.changes:
                return
            apply_tool_fixes(session, paths)

    def _review(self, session: Session, members: list[RoleSpec]) -> None:
        reviewers = [m for m in members if m.kind == "reviewer"]
        if not reviewers:
            return
        if len(session.changes):
            subject = [(c.action, c.path, c.content) for c in session.changes.changes()]
            label = "Proposed changes"
        else:  # called on its own: review what already exists
            paths = session.provider.relevant_files(session.request.task)
            subject = [("existing", p, session.workspace.read_text(p) or "") for p in paths]
            label = "Existing workspace files"
        for role in reviewers:
            self._started(session, role, "review")
            output = session.executor(role).review(session.request, session.board, subject, label=label)
            session.record(output, self.division.id)
            session.findings.extend(output.findings)


def self_check_rounds() -> int:
    """``CODING_AGENT_SELF_CHECK``: how many times a builder may fix its own files (default 1, 0 = off)."""
    from .env import env_value, load_local_env

    load_local_env()
    raw = env_value("CODING_AGENT_SELF_CHECK")
    try:
        return max(0, int(raw)) if raw not in (None, "") else 1
    except ValueError:
        return 1


# -------------------------------------------------------------------- phases
def group_by_division(tree: Tree, roles: list[RoleSpec], *, attach: bool) -> dict[str, list[RoleSpec]]:
    """Group roles by division, in division order, applying the agent's attachments."""
    attachments = dict(tree.root.attachments) if attach else {}
    groups: dict[str, list[RoleSpec]] = {d.id: [] for d in tree.divisions}
    for role in roles:
        host = attachments.get(role.id)
        target = host if host and any(r.division == host for r in roles) else role.division
        groups.setdefault(target, []).append(role)
    return {d: members for d, members in groups.items() if members}


def run_phases(session: Session, groups: dict[str, list[RoleSpec]], max_repairs: int) -> None:
    """analyze -> plan -> design -> build -> files gate/repair -> review -> optional fix."""
    session.team_groups = groups
    project_brief = getattr(session.provider, "project_brief", None)
    if callable(project_brief):
        summary = project_brief()
        if summary.text:
            session.emit({"event": "brief", "open_issues": summary.open_issues, "left_open": summary.left_open,
                          "history": summary.history, "requirements": summary.requirements})
    for phase in PHASES:
        if phase == "review":
            verify_and_repair(session, max_repairs)
        active = [d for d, members in groups.items() if _active(session, members, phase)]
        if not active:
            continue
        session.emit({"event": "phase", "phase": phase, "divisions": active})
        for division_id in active:
            DivisionRunner(session.tree.units[division_id]).run_phase(session, groups[division_id], phase)

    repairer = session.tree.by_id.get(REPAIRER)
    serious = [f for f in session.findings
               if f.severity in ("high", "critical") and f.file in session.changes.paths()]
    if session.request.fix_review_findings and serious and repairer is not None:
        session.emit({"event": "phase", "phase": "fix", "divisions": [repairer.division]})
        problems = [{"path": f.file, "check": f"review:{f.role}", "detail": f"{f.issue} Suggested: {f.suggestion}"}
                    for f in serious]
        output = session.executor(repairer).repair(session.request, problems, session.changes, session.board)
        session.record(output, repairer.division)
        verify_and_repair(session, max_repairs)


def _active(session: Session, members: list[RoleSpec], phase: str) -> bool:
    lead = session.tree.root.lead
    builders = any(m.kind in BUILDER_KINDS for m in members)
    if phase == "build":
        return builders
    if phase == "plan" and not builders and any(m.kind == "coordinator" for m in members):
        return True
    return any(m.phase == phase and m.id != lead and m.kind != "coordinator" for m in members)


def _check(session: Session, repairer: UnitSpec | None, test_mode: str) -> VerificationResult:
    """The tools' fixes, the files gate, then (when it passes) migrations and the Debugging Specialist's test run."""
    apply_tool_fixes(session)
    verification = verify_files(session.changes, session.work_items)
    session.emit({"event": "verification", "passed": verification.passed, "problems": verification.problems})
    if not verification.passed or repairer is None or not testrun.has_python(session.changes):
        return verification
    if test_mode == "off":
        session.note_test("test run skipped: CODING_AGENT_TEST_RUN=off, so the generated code was never run")
        return verification
    seconds = testrun.timeout()
    python_env = testrun.prepare_python(session.changes)
    if python_env.note or python_env.warning:
        if (python_env.note, python_env.warning) != session.test_env_shown:
            session.emit({"event": "test_env", "note": python_env.note, "warning": python_env.warning})
            session.test_env_shown = (python_env.note, python_env.warning)
        if python_env.warning:
            verification.checks.append(f"test environment: {python_env.warning}")
    if python_env.problem:
        # The requirements cannot be installed at all: fix that first; commands would only fail on imports.
        verification.problems.append({"path": python_env.problem_path, "check": "test-run",
                                      "detail": python_env.problem})
        verification.passed = False
        return verification
    _generate_migrations(session, python_env, verification)
    _import_check(session, python_env, verification, seconds)
    if session.test_commands is None:
        session.emit({"event": "role_started", "division": repairer.division, "role": repairer.id,
                      "phase": "test-plan"})
        commands, output = session.specialist(repairer).plan_test_run(session.request, session.changes)
        session.record(output, repairer.division)
        session.test_commands = commands or testrun.fallback_commands(session.changes)
    if not session.test_commands:
        session.note_test("test run: no commands were planned, so only the import check ran the code")
        verification.passed = not verification.problems
        return verification
    results = testrun.run_commands(session.changes, session.test_commands, seconds, python_env)
    session.test_results = results
    problems = testrun.problems_from(results, session.changes, seconds)
    failed = sum(r.failed for r in results)
    skipped = sum(bool(r.skipped) for r in results)
    if results and skipped == len(results):
        session.note_test("test run: every planned command was refused ("
                          + "; ".join(dict.fromkeys(r.skipped for r in results))[:200] + ")")
    verification.checks.append(f"test run: {len(results)} command(s), {failed} failed, {skipped} skipped")
    session.emit({"event": "test_run", "results": [r.to_dict() for r in results]})
    verification.problems += problems
    verification.passed = not verification.problems
    return verification


def _import_check(session: Session, python_env: testrun.PythonEnv, verification: VerificationResult,
                  seconds: float) -> None:
    """Import every project module in the sandbox (Django: after setup, then its system checks).

    Failures involving files this run changed go to repair; failures in untouched code are warnings.
    """
    result = testrun.import_check(session.changes, python_env, max(60.0, seconds * 3))
    if not result.ran:
        if result.note:
            verification.checks.append(f"import check not run: {result.note}")
        return
    mine = [f for f in result.failures if f["changed"]]
    older = [f for f in result.failures if not f["changed"]]
    verification.checks.append(f"import check: {len(result.modules)} module(s), {len(result.failures)} failed"
                               + (f" ({result.note})" if result.note else ""))
    session.emit({"event": "import_check", "modules": len(result.modules), "note": result.note,
                  "failures": [{"module": f["module"], "path": f["path"], "changed": bool(f["changed"]),
                                "error": f["detail"].splitlines()[0]} for f in result.failures]})
    verification.problems += [{"path": f["path"], "check": "import-run", "detail": f["detail"]} for f in mine]
    for failure in older:
        note = f"import check: {failure['detail'].splitlines()[0]} (in {failure['path']}, which this run did not change)"
        if note not in session.import_notes:
            session.import_notes.append(note)


def _generate_migrations(session: Session, python_env: testrun.PythonEnv, verification: VerificationResult) -> None:
    """Django migrations for the proposed models, made by Django itself (makemigrations), not by a model."""
    previous = set(session.migrations)
    for path in previous:  # regenerate from scratch: the models may have changed since
        change = next((c for c in session.changes.changes() if c.path == path), None)
        if change is not None and change.author_role == autofix.TOOL_ROLE:
            session.changes.discard(path)
    created, error = testrun.generate_migrations(session.changes, python_env)
    if error:
        verification.checks.append("makemigrations failed: " + error.splitlines()[-1][:160])
        return
    if not created:
        return
    session.migrations = set(created)
    if set(created) == previous:
        for path, content in created.items():  # same files again: put them back quietly
            session.changes.add(FileChange(path, "create", content, author_role=autofix.TOOL_ROLE,
                                           work_item_id=autofix.TOOL_ITEM))
        return
    session.add_tool_changes([(path, content, "generated by Django's makemigrations")
                              for path, content in sorted(created.items())], phase="migrations")
    verification.checks.append(f"migrations generated: {', '.join(sorted(created))}")


def verify_and_repair(session: Session, max_repairs: int) -> None:
    if not session.builders_ran and not len(session.changes):
        return
    repairer = session.tree.by_id.get(REPAIRER)
    test_mode = testrun.mode()
    verification = _check(session, repairer, test_mode)
    attempt = 0
    while not verification.passed and attempt < max_repairs and repairer is not None:
        attempt += 1
        session.emit({"event": "role_started", "division": repairer.division, "role": repairer.id,
                      "phase": "repair", "attempt": attempt})
        output = session.executor(repairer).repair(
            session.request, verification.problems, session.changes, session.board)
        session.record(output, repairer.division)
        verification = _check(session, repairer, test_mode)
        verification.checks.append(f"after repair attempt {attempt}")
    # Soft problems left after repairs are reported, not blocking: removing code may be what the task wants,
    # and a planned test command can be wrong too (unless CODING_AGENT_TEST_RUN=block).
    # The import check runs the code like the test run does, so it follows the same mode.
    soft = set(SOFT_CHECKS) | ({"test-run", "import-run"} if test_mode != "block" else set())
    leftover = [p for p in verification.problems if p["check"] in soft]
    if leftover:
        verification.problems = [p for p in verification.problems if p["check"] not in soft]
        verification.passed = not verification.problems
        labels = {"test-run": "test run", "import-run": "import check"}
        session.test_warnings = [
            (f"{labels[p['check']]}: {p['detail'].splitlines()[0]} (in {p['path']})" if p["check"] in labels
             else f"{p['path']}: {p['detail'].split('. Keep')[0]}")
            for p in leftover]
    session.verification = verification


# -------------------------------------------------------------------- finish
def finish(session: Session, report: RunReport, *, remember: bool) -> RunReport:
    """Gate result -> apply or propose -> artifacts -> memory. Entry unit only."""
    report.outputs = session.outputs
    report.changes = session.changes.changes()
    report.verification = session.verification
    report.advisory_findings = session.findings
    passed = report.verification is None or report.verification.passed

    failed = [o for o in session.outputs if o.error]
    report.warnings = [f"{o.role} ({o.phase}) failed: {o.error[:200]}" for o in failed]
    report.warnings += [f"{o.role} ({o.phase}): {c}" for o in session.outputs for c in o.concerns
                        if c.startswith(("rejected file", "rejected ", "failed: "))]
    report.warnings += session.test_warnings
    report.warnings += session.test_notes
    report.warnings += session.import_notes
    if session.test_env_shown[1]:
        report.warnings.append(f"test run: dependencies not installed: {session.test_env_shown[1]}")
    report.test_run = [r.to_dict() for r in session.test_results]
    report.notes = [f"fixed by tools: {note}" for note in session.tool_notes]
    # A specialist whose model failed may still deliver (e.g. a reviewer's deterministic tools).
    empty = [o for o in failed if not (o.changes or o.findings or o.work_items or o.decisions)]
    everyone_failed = bool(session.outputs) and len(empty) == len(session.outputs)

    if session.plan_failures:
        report.error = ("No usable plan from " + "; ".join(session.plan_failures)
                        + ". Nothing was written: without a plan, specialists end up writing files outside their part.")
    elif everyone_failed and not report.changes:
        report.error = f"Every specialist failed ({len(failed)}): " + "; ".join(
            f"{o.role}: {o.error[:120]}" for o in failed)
    elif session.builders_ran and not report.changes:
        report.error = "The team produced no file changes." + (
            f" Failures: {'; '.join(w for w in report.warnings)[:400]}" if report.warnings else "")
    elif not passed:
        report.error = "Generated files did not pass the files gate after repairs; nothing was written."
    elif report.changes and session.request.mode == "apply":
        session.workspace.apply(report.changes, session.run_dir)
        report.status = "completed"
        report.notes += projectdocs.record_run(session.workspace, report, session.run_dir)
    elif report.changes:
        report.status = "proposed"
    else:
        report.status = "completed"  # advisory-only units (planning, design, review)

    if report.status != "completed" and report.changes:
        save_proposed(session.run_dir, report)
    session.board.write(session.run_dir / "handoff.md",
                        f"# Handoff ({session.level}: {session.unit_id}): {session.request.task.strip()[:120]}")
    if remember and report.status == "completed" and (report.changes or session.outputs):
        session.provider.remember(memory_entry(session.request, report, session.board.decisions()))
    return report


def save_proposed(run_dir: Path, report: RunReport) -> None:
    for change in report.changes:
        if change.action == "delete":
            continue
        target = run_dir / "proposed" / change.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.content, encoding="utf-8", newline="\n")


def write_report(run_dir: Path, report: RunReport) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        data = report.to_dict()
        for change in data["changes"]:
            change["content_chars"] = len(change.pop("content", ""))
        for output in data["outputs"]:
            for change in output["changes"]:
                change.pop("content", None)
        (run_dir / "report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("could not write run report: %s", exc)


def memory_entry(request: CodingRequest, report: RunReport, decisions: list[str]) -> str:
    lines = [f"Unit: {report.unit_level} {report.unit_id}", f"Task: {request.task.strip()[:300]}",
             f"Team: {', '.join(report.team)}"]
    if report.changes:
        lines.append("Files: " + ", ".join(c.path for c in report.changes[:30]))
    lines += [f"- {d}" for d in decisions[:12]]
    lines += [f"- Finding [{f.severity}] {f.file or '-'}: {f.issue}" for f in report.advisory_findings[:8]]
    return "\n".join(lines)
