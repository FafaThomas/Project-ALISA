"""The unit hierarchy: Faction > Agent > Division > Specialist.

Every level is a *unit* with the same entry point,
``run(request, invocation, emit=None) -> RunReport``, so a caller can address
the whole coding agent, one division, or one specialist.

Rules that hold at every level:

- **Requests go down, reports come up.** A unit hands its members the request
  plus everything already on the shared blackboard, and collects their output.
- **Only the unit you called writes to disk.** That unit opens a
  ``Session``, runs the files gate and repairs, then applies changes (or
  keeps them as a proposal). Units below it only add to the session's
  proposed change set.
- **Context is inherited.** The session's context provider comes from the
  caller's invocation: Forge RAG only when the Forge called, Markdown otherwise.
- **Leads split work only when there is work to split.** A division lead is
  used for the build phase when the division has two or more builders
  (Development's Tech Lead whenever Development builds). Otherwise each
  builder takes its part directly, saving a model call.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol

from .blackboard import Blackboard
from .context import build_context_provider
from .context.base import ContextProvider
from .llm import ModelRouter
from .models import CodingRequest, Finding, InvocationContext, RoleOutput, RunReport, VerificationResult, WorkItem
from .registry import AGENT, DIVISIONS, PHASES, ROLES, DivisionSpec, RoleSpec
from .selection import division_needs_lead
from .specialist import Specialist
from .verification import verify_files
from .workspace import ChangeSet, Workspace

LOGGER = logging.getLogger(__name__)
Level = Literal["faction", "agent", "division", "specialist"]
Emit = Callable[[dict], None]
REPAIRER = "debugging_specialist"


class Unit(Protocol):
    """What every level of the hierarchy offers its callers."""

    level: Level
    unit_id: str
    name: str

    def run(self, request: CodingRequest, invocation: InvocationContext, *, emit: Emit | None = None) -> RunReport:
        ...


# ------------------------------------------------------------------- session
@dataclass
class Session:
    """State of one run, owned by the unit that was called."""

    request: CodingRequest
    invocation: InvocationContext
    router: ModelRouter
    workspace: Workspace
    provider: ContextProvider
    emit: Emit
    run_id: str
    run_dir: Path
    level: Level
    unit_id: str
    changes: ChangeSet = field(init=False)
    board: Blackboard = field(default_factory=Blackboard)
    outputs: list[RoleOutput] = field(default_factory=list)
    work_items: list[WorkItem] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    builders_ran: bool = False
    verification: VerificationResult | None = None

    def __post_init__(self) -> None:
        self.changes = ChangeSet(self.workspace)

    @classmethod
    def open(
        cls, request: CodingRequest, invocation: InvocationContext, router: ModelRouter, *,
        level: Level, unit_id: str, emit: Emit | None = None,
    ) -> "Session":
        workspace = Workspace(request.workspace)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        return cls(
            request=request, invocation=invocation, router=router, workspace=workspace,
            provider=build_context_provider(workspace, invocation), emit=emit or (lambda event: None),
            run_id=run_id, run_dir=workspace.agent_dir / "runs" / run_id, level=level, unit_id=unit_id,
        )

    def new_report(self) -> RunReport:
        return RunReport(
            run_id=self.run_id, status="failed", task=self.request.task, workspace=str(self.workspace.root),
            invocation_source=self.invocation.source.value, context_strategy=self.provider.strategy,
            team=[], unit_level=self.level, unit_id=self.unit_id, artifacts_dir=str(self.run_dir),
        )

    def specialist(self, role: RoleSpec) -> Specialist:
        return Specialist(role, self.router, self.provider, workspace_root=self.workspace.root,
                          division=DIVISIONS[role.division])

    def record(self, output: RoleOutput, division: str) -> None:
        self.outputs.append(output)
        self.board.post(output)
        self.emit({
            "event": "role_finished", "division": division, "role": output.role, "phase": output.phase,
            "summary": output.summary, "error": output.error, "files": [c.path for c in output.changes],
        })


# ------------------------------------------------------------------ division
class DivisionRunner:
    """Runs one division's members for one phase."""

    def __init__(self, division: DivisionSpec) -> None:
        self.division = division

    def _started(self, session: Session, role: RoleSpec, phase: str, **extra) -> None:
        session.emit({"event": "role_started", "division": self.division.id, "role": role.id, "phase": phase,
                      **extra})

    def run_phase(self, session: Session, members: list[RoleSpec], phase: str) -> None:
        if phase == "build":
            return self._build(session, members)
        if phase == "review":
            return self._review(session, members)
        builders = [m for m in members if m.kind in ("builder", "repairer")]
        for role in members:
            consults = role.phase == phase and role.kind in ("analyst", "planner", "designer")
            # A Tech Lead with nobody to assign work to reports its plan instead.
            lone_coordinator = role.kind == "coordinator" and phase == "plan" and not builders
            if role.id == AGENT.lead or not (consults or lone_coordinator):
                continue
            self._started(session, role, phase)
            session.record(session.specialist(role).consult(session.request, session.board), self.division.id)

    def _build(self, session: Session, members: list[RoleSpec]) -> None:
        builders = [m for m in members if m.kind in ("builder", "repairer")]
        if not builders:
            return
        session.builders_ran = True
        items: list[WorkItem] = []
        # A specialist called on its own works alone; it does not pull in its lead.
        if session.level != "specialist" and division_needs_lead(self.division, builders):
            lead = ROLES[self.division.lead]
            self._started(session, lead, "build", lead=True)
            plan = session.specialist(lead).coordinate(session.request, session.board, builders, session.changes)
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
            role = ROLES[item.owner_role]
            self._started(session, role, "build", work_item=item.id)
            output = session.specialist(role).build(session.request, session.board, item, items, session.changes)
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
            output = session.specialist(role).review(session.request, session.board, subject, label=label)
            session.record(output, self.division.id)
            session.findings.extend(output.findings)


# -------------------------------------------------------------------- phases
def group_by_division(roles: list[RoleSpec], *, attach: bool) -> dict[str, list[RoleSpec]]:
    """Group roles by division, in division order, applying agent attachments."""
    attachments = dict(AGENT.attachments) if attach else {}
    groups: dict[str, list[RoleSpec]] = {d: [] for d in DIVISIONS}
    for role in roles:
        host = attachments.get(role.id)
        target = host if host and any(r.division == host for r in roles) else role.division
        groups[target].append(role)
    return {d: members for d, members in groups.items() if members}


def run_phases(session: Session, groups: dict[str, list[RoleSpec]], max_repairs: int) -> None:
    """analyze -> plan -> design -> build -> files gate/repair -> review -> optional fix."""
    for phase in PHASES:
        if phase == "review":
            verify_and_repair(session, max_repairs)
        active = [d for d, members in groups.items() if _active(members, phase)]
        if not active:
            continue
        session.emit({"event": "phase", "phase": phase, "divisions": active})
        for division_id in active:
            DivisionRunner(DIVISIONS[division_id]).run_phase(session, groups[division_id], phase)

    serious = [f for f in session.findings
               if f.severity in ("high", "critical") and f.file in session.changes.paths()]
    if session.request.fix_review_findings and serious:
        session.emit({"event": "phase", "phase": "fix", "divisions": ["quality"]})
        problems = [{"path": f.file, "check": f"review:{f.role}", "detail": f"{f.issue} Suggested: {f.suggestion}"}
                    for f in serious]
        output = session.specialist(ROLES[REPAIRER]).repair(session.request, problems, session.changes, session.board)
        session.record(output, "quality")
        verify_and_repair(session, max_repairs)


def _active(members: list[RoleSpec], phase: str) -> bool:
    builders = any(m.kind in ("builder", "repairer") for m in members)
    if phase == "build":
        return builders
    if phase == "plan" and not builders and any(m.kind == "coordinator" for m in members):
        return True
    return any(m.phase == phase and m.id != AGENT.lead and m.kind != "coordinator" for m in members)


def verify_and_repair(session: Session, max_repairs: int) -> None:
    if not session.builders_ran and not len(session.changes):
        return
    verification = verify_files(session.changes, session.work_items)
    session.emit({"event": "verification", "passed": verification.passed, "problems": verification.problems})
    attempt = 0
    while not verification.passed and attempt < max_repairs:
        attempt += 1
        session.emit({"event": "role_started", "division": "quality", "role": REPAIRER, "phase": "repair",
                      "attempt": attempt})
        output = session.specialist(ROLES[REPAIRER]).repair(
            session.request, verification.problems, session.changes, session.board)
        session.record(output, "quality")
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
