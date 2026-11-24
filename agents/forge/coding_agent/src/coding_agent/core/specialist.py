"""Runs one specialist: its folder definition, a model tier and a context provider.

A specialist's optional ``tools.py`` can define:

- ``extra_context(query: str, workspace_root: Path) -> str | None``
      added to the specialist's context before the model is called
- ``review(files: list[tuple[str, str]]) -> list[dict]``
      deterministic findings over (path, content) pairs; each dict has
      ``severity``, ``issue`` and optionally ``file`` and ``suggestion``.
      These run even if the model call fails.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .blackboard import Blackboard
from .context.base import ContextProvider
from .llm import LLMError, ModelRouter
from .models import CodingRequest, FileChange, Finding, RoleOutput, WorkItem
from .prompts import SCHEMA_BY_KIND, WORK_ITEMS_SCHEMA, system_prompt
from .registry import ROLES, DivisionSpec, RoleSpec
from .workspace import ChangeSet, WorkspaceError

LOGGER = logging.getLogger(__name__)
# Roughly 3 characters per token leaves room for output in a 16k-token window.
DEFAULT_PROMPT_BUDGET = 36_000
SEVERITIES = ("info", "low", "medium", "high", "critical")


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]


def request_md(request: CodingRequest) -> str:
    parts = [f"# Task\n{request.task.strip()}"]
    if request.constraints:
        parts.append("## Constraints\n" + "\n".join(f"- {c}" for c in request.constraints))
    if request.acceptance_criteria:
        parts.append("## Acceptance criteria\n" + "\n".join(f"- {c}" for c in request.acceptance_criteria))
    return "\n\n".join(parts)


def division_md(division: DivisionSpec | None) -> str:
    if division is None:
        return ""
    return f"## Your division: {division.name}\n{division.mission}"


def _finding(role: str, raw: Any) -> Finding | None:
    if not isinstance(raw, dict) or not str(raw.get("issue") or "").strip():
        return None
    severity = str(raw.get("severity") or "info").lower()
    return Finding(
        role=role,
        severity=severity if severity in SEVERITIES else "info",  # type: ignore[arg-type]
        issue=str(raw["issue"]).strip(),
        file=str(raw.get("file") or "").strip(),
        suggestion=str(raw.get("suggestion") or "").strip(),
    )


class Specialist:
    def __init__(
        self, role: RoleSpec, router: ModelRouter, provider: ContextProvider, *,
        workspace_root: Path | None = None, division: DivisionSpec | None = None,
        prompt_budget: int = DEFAULT_PROMPT_BUDGET,
    ) -> None:
        self.role = role
        self.router = router
        self.provider = provider
        self.workspace_root = workspace_root
        self.division = division
        self.prompt_budget = prompt_budget

    # ------------------------------------------------------------------ core
    def _tool(self, name: str):
        try:
            tools = self.role.tools()
        except Exception as exc:  # a broken tools.py must not break the run
            LOGGER.warning("tools.py for %s failed to load: %s", self.role.id, exc)
            return None
        return getattr(tools, name, None) if tools else None

    def _ask(
        self, request: CodingRequest, sections: list[str], *, query: str,
        focus_files: list[str] | None = None, reader=None, as_lead: bool = False,
    ) -> dict[str, Any]:
        fixed_parts = [request_md(request), division_md(self.division), *sections]
        extra = self._tool("extra_context")
        if extra and self.workspace_root is not None:
            try:
                text = extra(query, self.workspace_root)
            except Exception as exc:
                LOGGER.warning("extra_context for %s failed: %s", self.role.id, exc)
                text = None
            if text:
                fixed_parts.append(f"## {self.role.name} tools\n{text}")
        fixed = "\n\n".join(p for p in fixed_parts if p)
        bundle = self.provider.gather(query, self.role, focus_files=focus_files, reader=reader)
        budget = max(2000, self.prompt_budget - len(fixed))
        prompt = f"{fixed}\n\n## Context ({bundle.strategy})\n{bundle.render(budget)}"
        schema = WORK_ITEMS_SCHEMA if as_lead else SCHEMA_BY_KIND[self.role.kind]
        client = self.router.client_for("planning" if as_lead else self.role.tier)
        return client.generate_json(system_prompt(self.role, as_lead=as_lead), prompt, schema=schema)

    def _output(self, phase: str, reply: dict[str, Any]) -> RoleOutput:
        return RoleOutput(
            role=self.role.id,
            phase=phase,
            summary=str(reply.get("summary") or "").strip(),
            decisions=_str_list(reply.get("decisions")),
            concerns=_str_list(reply.get("concerns")),
        )

    def _failed(self, phase: str, exc: Exception) -> RoleOutput:
        return RoleOutput(role=self.role.id, phase=phase, summary="", error=str(exc))

    # --------------------------------------------------------------- actions
    def consult(self, request: CodingRequest, board: Blackboard) -> RoleOutput:
        """Notes and decisions, no files (analysts, planners, designers)."""
        phase = "plan" if self.role.kind == "coordinator" else self.role.phase
        try:
            reply = self._ask(request, [f"## Team notes so far\n{board.render()}"], query=request.task)
        except LLMError as exc:
            return self._failed(phase, exc)
        output = self._output(phase, reply)
        if self.role.kind == "coordinator":  # a Tech Lead consulted alone reports its plan as decisions
            for raw in reply.get("work_items") or []:
                if isinstance(raw, dict) and raw.get("title"):
                    output.decisions.append(f"{raw.get('title')}: {', '.join(_str_list(raw.get('files')))}")
        return output

    def coordinate(
        self, request: CodingRequest, board: Blackboard, builders: list[RoleSpec], changes: ChangeSet,
    ) -> RoleOutput:
        """Division lead: split the division's work into owned work items."""
        roster = "\n".join(f"- {b.id}: {b.name} ({b.responsibility})" for b in builders)
        produced = "\n".join(f"- {p}" for p in changes.paths()) or "(none yet)"
        sections = [
            f"## Team notes so far\n{board.render()}",
            f"## Files other divisions already produced this run\n{produced}",
            f"## Specialists you can assign (owner_role must be one of these ids)\n{roster}",
        ]
        try:
            reply = self._ask(request, sections, query=request.task, as_lead=True)
        except LLMError as exc:
            return self._failed("build", exc)
        output = self._output("build", reply)
        valid = {b.id for b in builders}
        seen_files: set[str] = set()
        raw_items = reply.get("work_items") if isinstance(reply.get("work_items"), list) else []
        for index, raw in enumerate(raw_items, start=1):
            if not isinstance(raw, dict):
                continue
            owner = str(raw.get("owner_role") or "").strip()
            if owner not in valid:
                output.concerns.append(f"work item {index} owner {owner!r} is not in this division's team; reassigned")
                owner = builders[0].id
            files = []
            for path in _str_list(raw.get("files")):
                if path not in seen_files:  # one owner per file
                    seen_files.add(path)
                    files.append(path)
            output.work_items.append(WorkItem(
                id=str(raw.get("id") or f"W{index}").strip() or f"W{index}",
                title=str(raw.get("title") or f"Work item {index}").strip(),
                owner_role=owner,
                description=str(raw.get("description") or "").strip(),
                files=files,
            ))
        return output

    def build(
        self, request: CodingRequest, board: Blackboard, item: WorkItem, all_items: list[WorkItem],
        changes: ChangeSet,
    ) -> RoleOutput:
        """Write the files for one work item."""
        others = "\n".join(
            f"- {w.id} ({ROLES[w.owner_role].name if w.owner_role in ROLES else w.owner_role}): "
            f"{w.title} [{', '.join(w.files)}]"
            for w in all_items if w is not item
        ) or "(none)"
        produced = "\n".join(f"- {p}" for p in changes.paths()) or "(none yet)"
        sections = [
            f"## Your work item {item.id}: {item.title}\n{item.description}\n\n"
            f"Files you own: {', '.join(item.files) or 'choose sensible paths'}",
            f"## Other work items (owned by teammates)\n{others}",
            f"## Files already produced this run\n{produced}",
            f"## Team decisions\n{board.render()}",
        ]
        query = f"{request.task}\n{item.title}\n{item.description}"
        try:
            reply = self._ask(request, sections, query=query, focus_files=item.files, reader=changes.current)
        except LLMError as exc:
            return self._failed("build", exc)
        return self._collect_files("build", reply, changes, item.id)

    def repair(
        self, request: CodingRequest, problems: list[dict[str, str]], changes: ChangeSet, board: Blackboard,
    ) -> RoleOutput:
        listing = "\n".join(f"- {p['path']} [{p['check']}]: {p['detail']}" for p in problems)
        files = sorted({p["path"] for p in problems})
        sections = [f"## Problems to fix\n{listing}", f"## Team decisions\n{board.render()}"]
        try:
            reply = self._ask(request, sections, query=listing, focus_files=files, reader=changes.current)
        except LLMError as exc:
            return self._failed("repair", exc)
        return self._collect_files("repair", reply, changes, "repair")

    def review(
        self, request: CodingRequest, board: Blackboard, subject: list[tuple[str, str, str]], *,
        limit: int = 6000, label: str = "Proposed changes",
    ) -> RoleOutput:
        """Review ``subject``: (action, path, content) triples."""
        blocks = []
        for action, path, content in subject:
            body = content if len(content) <= limit else content[:limit] + "\n... [truncated]"
            blocks.append(f"### {action} {path}\n```\n{body}\n```")
        sections = [f"## Team decisions\n{board.render()}", f"## {label}\n" + ("\n\n".join(blocks) or "(none)")]
        try:
            reply = self._ask(request, sections, query=request.task)
            output = self._output("review", reply)
            raw_findings = reply.get("findings") or []
        except LLMError as exc:
            output, raw_findings = self._failed("review", exc), []
        output.findings.extend(f for f in (_finding(self.role.id, r) for r in raw_findings) if f)

        tool = self._tool("review")
        if tool:
            try:
                tool_findings = tool([(path, content) for action, path, content in subject if action != "delete"])
            except Exception as exc:
                LOGGER.warning("review tool for %s failed: %s", self.role.id, exc)
                tool_findings = []
            output.findings.extend(f for f in (_finding(self.role.id, r) for r in tool_findings or []) if f)
        return output

    def _collect_files(self, phase: str, reply: dict[str, Any], changes: ChangeSet, work_item: str) -> RoleOutput:
        output = self._output(phase, reply)
        output.decisions += _str_list(reply.get("notes"))
        for raw in reply.get("files") or []:
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action") or "create").lower()
            if action not in ("create", "modify", "delete"):
                action = "create"
            content = raw.get("content") if isinstance(raw.get("content"), str) else ""
            try:
                change = changes.add(FileChange(
                    path=str(raw.get("path") or ""), action=action,  # type: ignore[arg-type]
                    content=_strip_fence(content), author_role=self.role.id, work_item_id=work_item,
                ))
            except WorkspaceError as exc:
                output.concerns.append(f"rejected file: {exc}")
                continue
            output.changes.append(change)
        return output


_FENCE = re.compile(r"^```[\w.+-]*\n(.*)\n```\s*$", re.DOTALL)


def _strip_fence(content: str) -> str:
    match = _FENCE.match(content.strip())
    text = match.group(1) if match else content
    return text if text.endswith("\n") or not text else text + "\n"
