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
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .blackboard import Blackboard
from .context import build_context_provider
from .context.base import ContextProvider
from .llm import ModelRouter
from .models import (
    CodingRequest, Finding, InvocationContext, InvocationSource, RoleOutput, RunReport,
    VerificationResult, WorkItem, file_change_from_dict, request_to_dict, role_output_from_dict,
    work_item_from_dict,
)
from .registry import PHASES, RoleSpec, Tree, UnitSpec
from .selection import division_needs_lead, score_units
from .specialist import Specialist
from .verification import verify_files
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
                   changes: ChangeSet) -> RoleOutput: ...
    def build(self, request: CodingRequest, board: Blackboard, item: WorkItem, all_items: list[WorkItem],
              changes: ChangeSet) -> RoleOutput: ...
    def repair(self, request: CodingRequest, problems: list[dict[str, str]], changes: ChangeSet,
               board: Blackboard) -> RoleOutput: ...
    def review(self, request: CodingRequest, board: Blackboard, subject: list[tuple[str, str, str]], *,
               label: str = ...) -> RoleOutput: ...


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

    def __post_init__(self) -> None:
        self.changes = ChangeSet(self.workspace)
        self.board = Blackboard(tree=self.tree)

    @classmethod
    def open(
        cls, request: CodingRequest, invocation: InvocationContext, router: ModelRouter, tree: Tree, *,
        unit: UnitSpec, emit: Emit | None = None, run_id: str | None = None,
    ) -> "Session":
        workspace = Workspace(request.workspace)
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
                   changes: ChangeSet) -> RoleOutput:
        return self.local.coordinate(request, board, builders, changes)

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

    def coordinate(self, request, board, builders, changes):
        return self._invoke("coordinate", "build", {"builders": [b.path for b in builders]}, board, changes)

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
        output = executor.coordinate(request, session.board, builders, session.changes)
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
            plan = session.executor(lead).coordinate(session.request, session.board, builders, session.changes)
            items = [i for i in plan.work_items if i.owner_role in {b.id for b in builders}]
            if not items:
                plan.concerns.append("no usable work items; each specialist takes its part directly")
            session.record(plan, self.division.id)
        if not items:
            items = [
                WorkItem(f"{self.division.id}-{b.id}", f"{b.name}: {self.division.name} part of the request",
                         b.id, f"Deliver the part of the request your role is responsible for ({b.responsibility}).")
                for b in builders
            ]
        taken = {w.id for w in session.work_items}
        for item in items:
            if item.id in taken:
                item.id = f"{self.division.id}-{item.id}"
            taken.add(item.id)
        session.work_items.extend(items)
        for item in items:
            role = session.tree.by_id[item.owner_role]
            self._started(session, role, "build", work_item=item.id)
            output = session.executor(role).build(session.request, session.board, item, items, session.changes)
            session.record(output, self.division.id)

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


def verify_and_repair(session: Session, max_repairs: int) -> None:
    if not session.builders_ran and not len(session.changes):
        return
    verification = verify_files(session.changes, session.work_items)
    session.emit({"event": "verification", "passed": verification.passed, "problems": verification.problems})
    repairer = session.tree.by_id.get(REPAIRER)
    attempt = 0
    while not verification.passed and attempt < max_repairs and repairer is not None:
        attempt += 1
        session.emit({"event": "role_started", "division": repairer.division, "role": repairer.id,
                      "phase": "repair", "attempt": attempt})
        output = session.executor(repairer).repair(
            session.request, verification.problems, session.changes, session.board)
        session.record(output, repairer.division)
        verification = verify_files(session.changes, session.work_items)
        verification.checks.append(f"after repair attempt {attempt}")
        session.emit({"event": "verification", "passed": verification.passed, "problems": verification.problems})
    session.verification = verification


# -------------------------------------------------------------------- finish
def finish(session: Session, report: RunReport, *, remember: bool) -> RunReport:
    """Gate result -> apply or propose -> artifacts -> memory. Entry unit only."""
    report.outputs = session.outputs
    report.changes = session.changes.changes()
    report.verification = session.verification
    report.advisory_findings = session.findings
    passed = report.verification is None or report.verification.passed

    if session.builders_ran and not report.changes:
        report.error = "The team produced no file changes."
    elif not passed:
        report.error = "Generated files did not pass the files gate after repairs; nothing was written."
    elif report.changes and session.request.mode == "apply":
        session.workspace.apply(report.changes, session.run_dir)
        report.status = "completed"
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
