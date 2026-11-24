"""Shared data contracts for the coding agent and its internal team."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable


class InvocationSource(str, Enum):
    """Who invoked the coding agent.

    The source decides which context-management strategy the team uses:
    only a Forge invocation may supply the Forge RAG pipeline. API, UI and CLI
    invocations run on Markdown/workspace context management.
    """

    API = "api"
    UI = "ui"
    CLI = "cli"
    FORGE = "forge"


@runtime_checkable
class KnowledgeRetriever(Protocol):
    """The slice of the Forge RAG pipeline the coding agent depends on.

    Matches ``knowledge_ingestion_service.retrieval.FactionRetriever.search``.
    Each hit is a mapping with at least ``text`` and, ideally, ``relative_path``,
    ``score`` and ``summary``.
    """

    def search(
        self, query: str, faction_ids: list[str], top_k: int = 5, source_id: str | None = None
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class InvocationContext:
    """Trusted runtime facts about the caller. Never built from request bodies."""

    source: InvocationSource
    caller_id: str = "anonymous"
    knowledge: KnowledgeRetriever | None = None
    knowledge_factions: tuple[str, ...] = ("forge",)
    knowledge_source_id: str | None = None

    def __post_init__(self) -> None:
        if self.knowledge is not None and self.source is not InvocationSource.FORGE:
            raise ValueError(
                "Only a Forge invocation may attach the Forge RAG pipeline; "
                f"got source={self.source.value!r}."
            )

    @property
    def rag_enabled(self) -> bool:
        return self.source is InvocationSource.FORGE and self.knowledge is not None


ApplyMode = Literal["propose", "apply"]


@dataclass
class CodingRequest:
    """A unit of work for the coding team."""

    task: str
    workspace: str
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    mode: ApplyMode = "apply"
    # Explicit role ids the caller wants on the team (still validated).
    requested_roles: list[str] = field(default_factory=list)
    # Optional team size cap; None uses the assembler default.
    max_team_size: int | None = None
    # Run reviewer findings back through developers once (opt-in).
    fix_review_findings: bool = False

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            raise ValueError("task must not be empty")
        if not self.workspace or not str(self.workspace).strip():
            raise ValueError("workspace must not be empty")
        if self.mode not in ("propose", "apply"):
            raise ValueError("mode must be 'propose' or 'apply'")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CodingRequest":
        """Build a request from an untrusted JSON payload (API / UI)."""
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        allowed = {
            "task", "workspace", "constraints", "acceptance_criteria", "mode",
            "requested_roles", "max_team_size", "fix_review_findings",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown request fields: {sorted(unknown)}")

        def str_list(name: str) -> list[str]:
            value = payload.get(name) or []
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{name} must be a list of strings")
            return value

        max_team = payload.get("max_team_size")
        if max_team is not None and (isinstance(max_team, bool) or not isinstance(max_team, int)):
            raise ValueError("max_team_size must be an integer")
        return cls(
            task=str(payload.get("task", "")),
            workspace=str(payload.get("workspace", "")),
            constraints=str_list("constraints"),
            acceptance_criteria=str_list("acceptance_criteria"),
            mode=payload.get("mode", "apply"),
            requested_roles=str_list("requested_roles"),
            max_team_size=max_team,
            fix_review_findings=bool(payload.get("fix_review_findings", False)),
        )


@dataclass
class FileChange:
    path: str
    action: Literal["create", "modify", "delete"]
    content: str = ""
    author_role: str = ""
    work_item_id: str = ""


@dataclass
class WorkItem:
    id: str
    title: str
    owner_role: str
    description: str = ""
    files: list[str] = field(default_factory=list)


@dataclass
class Finding:
    role: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    issue: str
    file: str = ""
    suggestion: str = ""


@dataclass
class RoleOutput:
    """What one specialist contributed in one phase."""

    role: str
    phase: str
    summary: str
    decisions: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    work_items: list[WorkItem] = field(default_factory=list)
    changes: list[FileChange] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    error: str = ""


@dataclass
class VerificationResult:
    passed: bool
    checks: list[str] = field(default_factory=list)
    problems: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RunReport:
    run_id: str
    status: Literal["completed", "proposed", "failed"]
    task: str
    workspace: str
    invocation_source: str
    context_strategy: str
    team: list[str]
    # Which unit was called: "agent", "division" or "specialist", and its id.
    unit_level: str = "agent"
    unit_id: str = "coding_agent"
    team_rationale: str = ""
    outputs: list[RoleOutput] = field(default_factory=list)
    changes: list[FileChange] = field(default_factory=list)
    verification: VerificationResult | None = None
    advisory_findings: list[Finding] = field(default_factory=list)
    error: str = ""
    artifacts_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
