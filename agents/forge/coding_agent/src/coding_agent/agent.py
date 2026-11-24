"""The coding agent and its addressable units.

    Faction     The Forge (wraps this agent; not part of this package)
    Agent       CodingAgent                    lead: Engineering Director
    Division    CodingAgent.division("security")   lead: from division.md
    Specialist  CodingAgent.specialist("dependency_auditor")

Each unit offers ``run(request, invocation, emit=None) -> RunReport``:

    agent = CodingAgent()
    agent.run(request, invocation)                                  # the whole agent
    agent.division("security").run(request, invocation)             # one division
    agent.specialist("dependency_auditor").run(request, invocation) # one specialist

Callers and context: the ALISA UI, the HTTP API and the CLI use Markdown
context. Only ``CodingAgent.forge_invocation(retriever)`` (the Forge) brings
the Forge RAG pipeline, and it is inherited by every unit below.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from .core.context import build_context_provider
from .core.llm import LLMClient, ModelRouter
from .core.models import (
    CodingRequest, InvocationContext, InvocationSource, KnowledgeRetriever, RoleOutput, RunReport,
)
from .core.registry import AGENT, DIVISIONS, ROLES, REGISTRY, DivisionSpec, RoleSpec
from .core.selection import Team, TeamAssembler
from .core.units import Emit, Level, Session, finish, group_by_division, memory_entry, run_phases, write_report
from .core.workspace import Workspace

LOGGER = logging.getLogger(__name__)
Select = Callable[[Session, list[str]], Team]


class _UnitBase:
    level: Level
    unit_id: str
    name: str

    def __init__(self, owner: "CodingAgent") -> None:
        self.owner = owner

    def _select(self, session: Session, files: list[str]) -> Team:  # pragma: no cover - abstract
        raise NotImplementedError

    def _attach(self) -> bool:
        return False

    def run(self, request: CodingRequest, invocation: InvocationContext, *, emit: Emit | None = None) -> RunReport:
        owner = self.owner
        session = Session.open(request, invocation, owner.router, level=self.level, unit_id=self.unit_id, emit=emit)
        report = session.new_report()
        session.emit({"event": "run_started", "run_id": session.run_id, "level": self.level, "unit": self.unit_id,
                      "context_strategy": session.provider.strategy, "source": invocation.source.value})
        try:
            team = self._select(session, session.workspace.list_files())
            report.team = team.ids
            report.team_rationale = team.rationale
            groups = group_by_division(team.roles, attach=self._attach())
            session.emit({"event": "team", "team": team.ids, "rationale": team.rationale,
                          "divisions": {d: [r.id for r in members] for d, members in groups.items()},
                          "standby": [r.id for r in team.standby]})
            run_phases(session, groups, owner.max_repairs)
            finish(session, report, remember=owner.remember_runs)
        except Exception as exc:  # report every failure instead of raising to callers
            LOGGER.exception("%s %s run %s failed", self.level, self.unit_id, session.run_id)
            report.status = "failed"
            report.error = f"{type(exc).__name__}: {exc}"
        finally:
            write_report(session.run_dir, report)
            session.emit({"event": "run_finished", "run_id": session.run_id, "status": report.status,
                          "error": report.error})
        return report


class DivisionUnit(_UnitBase):
    level: Level = "division"

    def __init__(self, owner: "CodingAgent", spec: DivisionSpec) -> None:
        super().__init__(owner)
        self.spec = spec
        self.unit_id = spec.id
        self.name = spec.name

    @property
    def lead(self) -> RoleSpec:
        return ROLES[self.spec.lead]

    @property
    def members(self) -> list[RoleSpec]:
        return [ROLES[m] for m in self.spec.members]

    def _select(self, session: Session, files: list[str]) -> Team:
        return TeamAssembler(None, use_director_llm=False).for_division(session.request, files, self.spec)


class SpecialistUnit(_UnitBase):
    level: Level = "specialist"

    def __init__(self, owner: "CodingAgent", role: RoleSpec) -> None:
        super().__init__(owner)
        self.role = role
        self.unit_id = role.id
        self.name = role.name

    def _select(self, session: Session, files: list[str]) -> Team:
        if self.role.id == AGENT.lead:
            # Calling the Engineering Director alone answers "who would you put on this?"
            team = TeamAssembler(self.owner.router, use_director_llm=self.owner.use_director_llm).assemble(
                session.request, files)
            session.record(RoleOutput(
                role=self.role.id, phase="plan", summary=team.rationale,
                decisions=[f"Team: {', '.join(team.ids)}"],
            ), self.role.division)
            return Team([self.role], team.rationale, [], team.scores)
        return Team([self.role], f"{self.role.name} called directly.")


class CodingAgent(_UnitBase):
    """The agent-level unit; also the factory for its divisions and specialists."""

    level: Level = "agent"

    def __init__(
        self,
        router: ModelRouter | None = None,
        *,
        use_director_llm: bool = True,
        max_repairs: int = 2,
        remember_runs: bool = True,
    ) -> None:
        super().__init__(self)
        self.router = router or ModelRouter()
        self.use_director_llm = use_director_llm
        self.max_repairs = max_repairs
        self.remember_runs = remember_runs
        self.unit_id = AGENT.id
        self.name = AGENT.name

    @classmethod
    def with_client(cls, client: LLMClient, **kwargs: Any) -> "CodingAgent":
        """Serve every tier from one client (tests, or a single hosted model)."""
        return cls(ModelRouter.single(client), **kwargs)

    @staticmethod
    def forge_invocation(
        retriever: KnowledgeRetriever, *, caller_id: str = "forge",
        factions: tuple[str, ...] = ("forge",), source_id: str | None = None,
    ) -> InvocationContext:
        """The invocation context the Forge uses to lend its RAG pipeline."""
        return InvocationContext(
            source=InvocationSource.FORGE, caller_id=caller_id, knowledge=retriever,
            knowledge_factions=factions, knowledge_source_id=source_id,
        )

    # ---------------------------------------------------------------- units
    def division(self, division_id: str) -> DivisionUnit:
        if division_id not in DIVISIONS:
            raise ValueError(f"unknown division {division_id!r}; choose from {sorted(DIVISIONS)}")
        return DivisionUnit(self, DIVISIONS[division_id])

    def specialist(self, specialist_id: str) -> SpecialistUnit:
        if specialist_id not in ROLES:
            raise ValueError(f"unknown specialist {specialist_id!r}")
        return SpecialistUnit(self, ROLES[specialist_id])

    def unit(self, level: str, unit_id: str | None = None) -> "_UnitBase":
        if level == "agent":
            return self
        if not unit_id:
            raise ValueError(f"a {level} id is required")
        if level == "division":
            return self.division(unit_id)
        if level == "specialist":
            return self.specialist(unit_id)
        raise ValueError(f"unknown level {level!r}")

    @staticmethod
    def describe() -> dict[str, Any]:
        """The hierarchy as data (for UIs and the API)."""
        return {
            "id": AGENT.id, "name": AGENT.name, "level": "agent", "lead": AGENT.lead,
            "attachments": dict(AGENT.attachments),
            "divisions": [
                {
                    "id": d.id, "name": d.name, "level": "division", "lead": d.lead, "mission": d.mission,
                    "specialists": [
                        {"id": r.id, "name": r.name, "level": "specialist", "kind": r.kind, "phase": r.phase,
                         "tier": r.tier, "responsibility": r.responsibility, "has_tools": r.has_tools}
                        for r in (ROLES[m] for m in d.members)
                    ],
                }
                for d in REGISTRY.divisions
            ],
        }

    # -------------------------------------------------------- agent behaviour
    def _select(self, session: Session, files: list[str]) -> Team:
        return TeamAssembler(self.router, use_director_llm=self.use_director_llm).assemble(session.request, files)

    def _attach(self) -> bool:
        return True

    def apply_proposal(self, report: RunReport, invocation: InvocationContext) -> RunReport:
        """Write a ``proposed`` run's changes after a human approved them."""
        if report.status != "proposed":
            raise ValueError(f"only proposed runs can be applied (status={report.status!r})")
        workspace = Workspace(report.workspace)
        run_dir = Path(report.artifacts_dir)
        workspace.apply(report.changes, run_dir)
        report.status = "completed"
        if self.remember_runs:
            decisions = [d for o in report.outputs for d in o.decisions]
            request = CodingRequest(task=report.task, workspace=report.workspace)
            build_context_provider(workspace, invocation).remember(memory_entry(request, report, decisions))
        write_report(run_dir, report)
        return report
