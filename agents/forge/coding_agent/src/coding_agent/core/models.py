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

# Chat turns an API caller may send with a run or a question.
MAX_HISTORY_TURNS = 20
MAX_TURN_CHARS = 4_000
# Each repair round is a model call and another check; more than this and a stuck run loops for long.
MAX_REPAIR_ROUNDS = 10


def parse_history(value: Any) -> list[dict[str, str]]:
    """Chat turns from an untrusted payload: the last MAX_HISTORY_TURNS, each cut to MAX_TURN_CHARS."""
    value = value or []
    if not isinstance(value, list) or not all(
            isinstance(t, dict) and t.get("role") in ("user", "agent") and isinstance(t.get("text"), str)
            for t in value):
        raise ValueError("history must be a list of {role: 'user' | 'agent', text: string}")
    return [{"role": t["role"], "text": t["text"][:MAX_TURN_CHARS]} for t in value[-MAX_HISTORY_TURNS:]]


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
    # Where the generated code must run, e.g. "Windows 11, Python 3.12". Empty: the
    # CODING_AGENT_TARGET_ENVIRONMENT setting, else the machine running the agent.
    target_environment: str = ""
    # The chat before this task ({role: "user" | "agent", text}, oldest first), so "fix it" can be
    # understood. Every specialist and the Director see its last turns; it is not recorded anywhere.
    history: list[dict[str, str]] = field(default_factory=list)
    # Rounds the Debugging Specialist gets to fix what the files gate or the test run found before the
    # run fails (0 to MAX_REPAIR_ROUNDS). None: the agent's setting (2, or the CLI's --max-repairs).
    max_repairs: int | None = None

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            raise ValueError("task must not be empty")
        if not self.workspace or not str(self.workspace).strip():
            raise ValueError("workspace must not be empty")
        if self.mode not in ("propose", "apply"):
            raise ValueError("mode must be 'propose' or 'apply'")
        if self.max_repairs is not None and (
                isinstance(self.max_repairs, bool) or not isinstance(self.max_repairs, int)
                or not 0 <= self.max_repairs <= MAX_REPAIR_ROUNDS):
            raise ValueError(f"max_repairs must be an integer from 0 to {MAX_REPAIR_ROUNDS}")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CodingRequest":
        """Build a request from an untrusted JSON payload (API / UI)."""
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        allowed = {
            "task", "workspace", "constraints", "acceptance_criteria", "mode",
            "requested_roles", "max_team_size", "fix_review_findings", "target_environment", "history",
            "max_repairs",
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
            target_environment=str(payload.get("target_environment") or ""),
            history=parse_history(payload.get("history")),
            max_repairs=payload.get("max_repairs"),  # checked in __post_init__
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
    # The model that produced this output and where the choice came from,
    # e.g. "unit.md (development/database_developer)" or ".env CODING_AGENT_OLLAMA_MODEL".
    model: str = ""
    model_source: str = ""
    # Tokens used by this output's model call(s): prompt, output, thinking (if any), total.
    tokens: dict[str, int] = field(default_factory=dict)


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
    # Specialists that failed while the run as a whole could continue.
    warnings: list[str] = field(default_factory=list)
    # The Debugging Specialist's test run (last round): command, exit code, hang, output tails.
    test_run: list[dict[str, Any]] = field(default_factory=list)
    artifacts_dir: str = ""
    # What the run did besides the changes: fixes made by tools, the changelog and project brief it updated.
    notes: list[str] = field(default_factory=list)
    # The request's requirements, kept in the project brief so later runs keep meeting them.
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Answer:
    """A unit's reply to a question about the workspace (chat). Nothing is written, nothing remembered."""

    role: str
    answer: str
    # Workspace files the answer refers to; only paths that exist are kept.
    files: list[str] = field(default_factory=list)
    error: str = ""
    model: str = ""
    model_source: str = ""
    tokens: dict[str, int] = field(default_factory=dict)
    # Things the caller should know, e.g. an open file that was not shared because it holds secrets.
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------ wire format
# Units deployed as their own services exchange these objects as JSON.
def work_item_from_dict(data: dict[str, Any]) -> WorkItem:
    return WorkItem(
        id=str(data.get("id", "")), title=str(data.get("title", "")), owner_role=str(data.get("owner_role", "")),
        description=str(data.get("description", "")), files=[str(f) for f in data.get("files") or []],
    )


def file_change_from_dict(data: dict[str, Any]) -> FileChange:
    action = data.get("action", "create")
    return FileChange(
        path=str(data.get("path", "")), action=action if action in ("create", "modify", "delete") else "create",
        content=str(data.get("content", "")), author_role=str(data.get("author_role", "")),
        work_item_id=str(data.get("work_item_id", "")),
    )


def finding_from_dict(data: dict[str, Any]) -> Finding:
    severity = data.get("severity", "info")
    return Finding(
        role=str(data.get("role", "")),
        severity=severity if severity in ("info", "low", "medium", "high", "critical") else "info",
        issue=str(data.get("issue", "")), file=str(data.get("file", "")), suggestion=str(data.get("suggestion", "")),
    )


def role_output_from_dict(data: dict[str, Any]) -> RoleOutput:
    return RoleOutput(
        role=str(data.get("role", "")), phase=str(data.get("phase", "")), summary=str(data.get("summary", "")),
        decisions=[str(d) for d in data.get("decisions") or []],
        concerns=[str(c) for c in data.get("concerns") or []],
        work_items=[work_item_from_dict(w) for w in data.get("work_items") or []],
        changes=[file_change_from_dict(c) for c in data.get("changes") or []],
        findings=[finding_from_dict(f) for f in data.get("findings") or []],
        error=str(data.get("error", "")), model=str(data.get("model", "")),
        model_source=str(data.get("model_source", "")),
        tokens={str(k): int(v) for k, v in (data.get("tokens") or {}).items()},
    )


def request_to_dict(request: CodingRequest) -> dict[str, Any]:
    return asdict(request)


def run_report_from_dict(data: dict[str, Any]) -> RunReport:
    """A report read back from ``report.json``. File contents are not in that file; callers add them."""
    verification = data.get("verification")
    status = data.get("status")
    return RunReport(
        run_id=str(data.get("run_id", "")),
        status=status if status in ("completed", "proposed", "failed") else "failed",
        task=str(data.get("task", "")), workspace=str(data.get("workspace", "")),
        invocation_source=str(data.get("invocation_source", "")),
        context_strategy=str(data.get("context_strategy", "")),
        team=[str(t) for t in data.get("team") or []],
        unit_level=str(data.get("unit_level", "agent")), unit_id=str(data.get("unit_id", "coding_agent")),
        team_rationale=str(data.get("team_rationale", "")),
        outputs=[role_output_from_dict(o) for o in data.get("outputs") or []],
        changes=[file_change_from_dict(c) for c in data.get("changes") or []],
        verification=VerificationResult(
            passed=bool(verification.get("passed")), checks=[str(c) for c in verification.get("checks") or []],
            problems=[{str(k): str(v) for k, v in p.items()} for p in verification.get("problems") or []],
        ) if isinstance(verification, dict) else None,
        advisory_findings=[finding_from_dict(f) for f in data.get("advisory_findings") or []],
        error=str(data.get("error", "")),
        warnings=[str(w) for w in data.get("warnings") or []],
        test_run=[t for t in data.get("test_run") or [] if isinstance(t, dict)],
        artifacts_dir=str(data.get("artifacts_dir", "")),
        notes=[str(n) for n in data.get("notes") or []],
        constraints=[str(c) for c in data.get("constraints") or []],
        acceptance_criteria=[str(c) for c in data.get("acceptance_criteria") or []],
    )
