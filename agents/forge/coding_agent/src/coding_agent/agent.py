"""The coding agent's addressable units.

    Faction         The Forge (wraps this agent; not part of this package)
    Agent           the coding agent                         lead: Engineering Director
    Division        e.g. "security"                          lead: from its unit.md
    Specialist      e.g. "development/database_developer"
    Sub-specialist  e.g. "development/database_developer/postgres_developer"

Any unit can be used on its own, without going through the agent:

    from coding_agent import load_unit
    load_unit("development/database_developer").run(request, invocation)
    load_unit("dependency_auditor").run(request, invocation)    # ids work too
    load_unit().run(request, invocation)                        # the whole agent

and any unit can be served as its own HTTP service (``coding-agent serve --unit``).

Callers and context: the ALISA UI, the HTTP API and the CLI use Markdown
context. Only ``forge_invocation(retriever)`` (the Forge) brings the Forge RAG
pipeline, and it is inherited by every unit below the one called.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .core.context import build_context_provider
from .core.projectdocs import record_run
from .core.llm import LLMClient, ModelRouter
from .core.models import (
    Answer, CodingRequest, InvocationContext, InvocationSource, KnowledgeRetriever, RoleOutput, RunReport,
)
from .core.registry import Tree, UnitSpec, default_tree
from .core.selection import Team, TeamAssembler
from .core.specialist import Specialist
from .core.units import Emit, Session, finish, group_by_division, memory_entry, run_phases, write_report
from .core.workspace import Workspace, WorkspaceError

LOGGER = logging.getLogger(__name__)
# Who answers questions put to the whole agent.
ASK_DEFAULT = "codebase_analyst"


class Runtime:
    """What every unit shares: the tree, the model router and run settings."""

    def __init__(
        self,
        router: ModelRouter | None = None,
        *,
        tree: Tree | None = None,
        use_director_llm: bool = True,
        max_repairs: int = 2,
        remember_runs: bool = True,
    ) -> None:
        self.router = router or ModelRouter()
        self.tree = tree or default_tree()
        self.use_director_llm = use_director_llm
        self.max_repairs = max_repairs
        self.remember_runs = remember_runs


class Unit:
    """One node of the tree. ``run`` makes it the unit that was called."""

    def __init__(self, spec: UnitSpec, runtime: Runtime) -> None:
        self.spec = spec
        self.runtime = runtime

    @property
    def level(self) -> str:
        return self.spec.level

    @property
    def unit_id(self) -> str:
        return self.spec.id

    @property
    def path(self) -> str:
        return self.spec.path

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def tree(self) -> Tree:
        return self.runtime.tree

    @property
    def router(self) -> ModelRouter:
        return self.runtime.router

    def describe(self) -> dict[str, Any]:
        return self.tree.describe(self.spec)

    # ------------------------------------------------------------ selection
    def _select(self, session: Session, files: list[str]) -> Team:
        tree, spec = self.tree, self.spec
        if spec.level == "agent":
            return TeamAssembler(self.router, tree=tree, use_director_llm=self.runtime.use_director_llm).assemble(
                session.request, files)
        if spec.level == "division":
            return TeamAssembler(None, tree=tree, use_director_llm=False).for_division(session.request, files, spec)
        if spec.id == tree.root.lead:
            # Calling the agent's lead alone answers "who would you put on this?"
            team = TeamAssembler(self.router, tree=tree, use_director_llm=self.runtime.use_director_llm).assemble(
                session.request, files)
            session.record(RoleOutput(role=spec.id, phase="plan", summary=team.rationale,
                                      decisions=[f"Team: {', '.join(team.ids)}"]), spec.division)
            return Team([spec], team.rationale, [], team.scores)
        return Team([spec], f"{spec.name} called directly.")

    # ------------------------------------------------------------------ run
    def run(self, request: CodingRequest, invocation: InvocationContext, *, emit: Emit | None = None) -> RunReport:
        session = Session.open(request, invocation, self.router, self.tree, unit=self.spec, emit=emit)
        report = session.new_report()
        session.emit({"event": "run_started", "run_id": session.run_id, "level": self.level, "unit": self.unit_id,
                      "path": self.path, "context_strategy": session.provider.strategy,
                      "source": invocation.source.value})
        try:
            team = self._select(session, session.workspace.list_files())
            report.team = team.ids
            report.team_rationale = team.rationale
            groups = group_by_division(self.tree, team.roles, attach=self.level == "agent")
            session.emit({"event": "team", "team": team.ids, "rationale": team.rationale,
                          "divisions": {d: [r.id for r in members] for d, members in groups.items()},
                          "standby": [r.id for r in team.standby]})
            run_phases(session, groups, self.runtime.max_repairs)
            finish(session, report, remember=self.runtime.remember_runs)
        except Exception as exc:  # report every failure instead of raising to callers
            LOGGER.exception("%s %s run %s failed", self.level, self.unit_id, session.run_id)
            report.status = "failed"
            report.error = f"{type(exc).__name__}: {exc}"
        finally:
            write_report(session.run_dir, report)
            session.emit({"event": "run_finished", "run_id": session.run_id, "status": report.status,
                          "error": report.error})
        return report

    # ------------------------------------------------------------------ ask
    def answerer(self) -> UnitSpec:
        """Who answers questions put to this unit: a specialist itself, a division's lead, and for the
        agent its Codebase Analyst (else its lead)."""
        spec, tree = self.spec, self.tree
        if spec.level == "specialist":
            return spec
        if spec.level == "agent":
            return tree.by_id.get(ASK_DEFAULT) or tree.by_id[spec.lead]
        if spec.lead and spec.lead in tree.by_id:
            return tree.by_id[spec.lead]
        members = [tree.by_id[m] for m in spec.default_members if m in tree.by_id] or tree.children(spec)
        if not members:
            raise ValueError(f"{spec.id} has no specialist to answer questions")
        return members[0]

    def ask(
        self, question: str, workspace: str, invocation: InvocationContext, *,
        history: list[dict[str, str]] | None = None, focus_files: list[str] | None = None,
        emit: Emit | None = None,
    ) -> Answer:
        """Answer a question about the workspace (chat). One model call; nothing is written or remembered.

        ``history`` holds earlier turns as ``{"role": "user" | "agent", "text": ...}``; ``focus_files``
        are workspace-relative files the user has open, shared with the answering specialist.
        """
        emit = emit or (lambda event: None)
        role = self.answerer()
        root = Workspace(workspace)
        if not root.exists:
            return Answer(role=role.id, answer="", error=f"workspace does not exist: {workspace}")
        shared, notes = [], []
        for rel in focus_files or []:
            try:
                shared.append(root.normalize(rel))
            except WorkspaceError as exc:
                notes.append(f"not shared: {exc}")
        request = CodingRequest(task=question, workspace=str(root.root), mode="propose")
        emit({"event": "ask_started", "role": role.id, "name": role.name, "files": shared})
        specialist = Specialist(role, self.tree, self.router, build_context_provider(root, invocation),
                                workspace_root=root.root)
        answer = specialist.answer(request, history, shared)
        answer.files = list(dict.fromkeys(f for f in (_existing_file(root, f) for f in answer.files) if f))
        answer.notes = notes + answer.notes
        emit({"event": "ask_finished", "role": role.id, "error": answer.error, "model": answer.model,
              "model_source": answer.model_source})
        return answer


def _existing_file(workspace: Workspace, relative: str) -> str | None:
    """A path a model mentioned, normalized, if the workspace guard accepts it and the file exists."""
    try:
        normalized = workspace.normalize(relative)
    except WorkspaceError:
        return None
    return normalized if (workspace.root / normalized).is_file() else None


# Names kept for readability at call sites.
DivisionUnit = Unit
SpecialistUnit = Unit


class CodingAgent(Unit):
    """The agent: the root unit, and the way to reach every unit below it."""

    def __init__(
        self,
        router: ModelRouter | None = None,
        *,
        tree: Tree | None = None,
        use_director_llm: bool = True,
        max_repairs: int = 2,
        remember_runs: bool = True,
    ) -> None:
        runtime = Runtime(router, tree=tree, use_director_llm=use_director_llm, max_repairs=max_repairs,
                          remember_runs=remember_runs)
        super().__init__(runtime.tree.root, runtime)

    @classmethod
    def with_client(cls, client: LLMClient, **kwargs: Any) -> "CodingAgent":
        """Serve every unit from one client (tests, or a single hosted model)."""
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
    def unit(self, ref: str | None = None) -> Unit:
        """Any unit by path ("development/python_developer") or id; empty for the agent itself."""
        if not ref:
            return self
        try:
            spec = self.tree.get(ref)
        except KeyError as exc:
            raise ValueError(str(exc).strip("'\"")) from None
        return self if spec.level == "agent" else Unit(spec, self.runtime)

    def division(self, division_id: str) -> Unit:
        unit = self.unit(division_id)
        if unit.level != "division":
            raise ValueError(f"{division_id!r} is not a division")
        return unit

    def specialist(self, specialist_id: str) -> Unit:
        unit = self.unit(specialist_id)
        if unit.level != "specialist":
            raise ValueError(f"{specialist_id!r} is not a specialist")
        return unit

    # ----------------------------------------------------------- approvals
    def apply_proposal(self, report: RunReport, invocation: InvocationContext) -> RunReport:
        """Write a ``proposed`` run's changes after a human approved them."""
        if report.status != "proposed":
            raise ValueError(f"only proposed runs can be applied (status={report.status!r})")
        workspace = Workspace(report.workspace)
        run_dir = Path(report.artifacts_dir)
        workspace.apply(report.changes, run_dir)
        report.status = "completed"
        report.notes += record_run(workspace, report, run_dir)
        if self.runtime.remember_runs:
            decisions = [d for o in report.outputs for d in o.decisions]
            request = CodingRequest(task=report.task, workspace=report.workspace)
            build_context_provider(workspace, invocation).remember(memory_entry(request, report, decisions))
        write_report(run_dir, report)
        return report


def load_unit(ref: str | None = None, **kwargs: Any) -> Unit:
    """Load one unit to use on its own. ``kwargs`` go to ``CodingAgent`` (router, tree, ...)."""
    return CodingAgent(**kwargs).unit(ref)
