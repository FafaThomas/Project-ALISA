"""Runs one specialist: its folder definition, a model and a context provider.

A specialist folder can change how it works in two places:

- ``agent.py``: a ``UnitAgent`` subclass overriding ``extra_context``,
  ``review`` and ``after_output`` (see core/hooks.py);
- ``tools.py`` (older, function-based) can define:

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
from .hooks import load_hooks
from .llm import LLMError, ModelRouter
from .models import CodingRequest, FileChange, Finding, RoleOutput, WorkItem
from .prompts import (
    SCHEMA_BY_KIND, TEST_PLAN_INSTRUCTIONS, TEST_PLAN_SCHEMA, WORK_ITEMS_SCHEMA, system_prompt,
)
from .pyinterface import signatures
from .registry import RoleSpec, Tree
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
    if request.target_environment:
        parts.append(f"## Target environment\n{request.target_environment}. The code must run there: use only "
                     "modules and APIs available on this platform unless the task says otherwise.")
    if request.constraints:
        parts.append("## Constraints\n" + "\n".join(f"- {c}" for c in request.constraints))
    if request.acceptance_criteria:
        parts.append("## Acceptance criteria\n" + "\n".join(f"- {c}" for c in request.acceptance_criteria))
    return "\n\n".join(parts)


def unit_context_md(role: RoleSpec, tree: Tree) -> str:
    """The division (and team) a specialist works in, from their unit.md briefs."""
    parts = []
    division = tree.division_of(role)
    if division is not None and division.mission:
        parts.append(f"## Your division: {division.name}\n{division.mission}")
    team = tree.team_of(role)
    if team is not None:
        parts.append(f"## Your team: {team.name}\n{team.responsibility}")
    return "\n\n".join(parts)


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


def produced_interfaces(changes: ChangeSet, exclude: set[str], budget: int = 5000) -> str:
    """What teammates already wrote this run: Python files with their signatures, others by name."""
    blocks: list[str] = []
    used = 0
    for path in changes.paths():
        if path in exclude:
            continue
        content = changes.current(path)
        if content is None:
            continue
        if path.endswith(".py"):
            block = f"- {path}\n" + "\n".join(f"    {line}" for line in signatures(content))
        else:
            block = f"- {path}"
        if used + len(block) > budget:
            blocks.append(f"- ... {len(changes.paths()) - len(blocks)} more file(s)")
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks) or "(none yet)"


class Specialist:
    def __init__(
        self, role: RoleSpec, tree: Tree, router: ModelRouter, provider: ContextProvider, *,
        workspace_root: Path | None = None, prompt_budget: int = DEFAULT_PROMPT_BUDGET,
    ) -> None:
        self.role = role
        self.tree = tree
        self.router = router
        self.provider = provider
        self.workspace_root = workspace_root
        self.prompt_budget = prompt_budget
        self.last_model = ""
        self.last_model_source = ""
        self.last_tokens: dict[str, int] = {}
        self.hooks = load_hooks(role.folder)

    # ------------------------------------------------------------------ core
    def _tool(self, name: str):
        try:
            tools = self.role.tools()
        except Exception as exc:  # a broken tools.py must not break the run
            LOGGER.warning("tools.py for %s failed to load: %s", self.role.id, exc)
            return None
        return getattr(tools, name, None) if tools else None

    def _extensions(self, name: str) -> list:
        """The unit's own implementations of ``name``: tools.py function, then agent.py hook."""
        found = [self._tool(name)]
        if self.hooks is not None:
            found.append(getattr(self.hooks, name, None))
        return [f for f in found if f is not None]

    def _after(self, output: RoleOutput, action: str) -> RoleOutput:
        if self.hooks is None:
            return output
        try:
            result = self.hooks.after_output(output, action)
        except Exception as exc:
            LOGGER.warning("after_output for %s failed: %s", self.role.id, exc)
            output.concerns.append(f"after_output hook failed: {exc}")
            return output
        return result if isinstance(result, RoleOutput) else output

    def _ask(
        self, request: CodingRequest, sections: list[str], *, query: str,
        focus_files: list[str] | None = None, reader=None, as_lead: bool = False,
        schema_override: dict[str, Any] | None = None, instructions: str | None = None,
    ) -> dict[str, Any]:
        fixed_parts = [request_md(request), unit_context_md(self.role, self.tree), *sections]
        for extra in self._extensions("extra_context") if self.workspace_root is not None else []:
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
        schema = schema_override or (WORK_ITEMS_SCHEMA if as_lead else SCHEMA_BY_KIND[self.role.kind])
        model, model_source = self.tree.effective_model(self.role)
        tier = "planning" if as_lead else self.role.tier
        client, source = self.router.resolve(tier, model, model_source)
        self.last_model, self.last_model_source = client.model, source
        think, _ = self.tree.effective_think(self.role)
        temperature, _ = self.tree.effective_temperature(self.role)
        self.last_tokens = {}
        system = system_prompt(self.role, self.tree, as_lead=as_lead, instructions=instructions)
        reply = client.generate_json(system, prompt, schema=schema, think=think, temperature=temperature)
        self.last_tokens = dict(getattr(client, "last_usage", {}) or {})
        used = getattr(client, "used_model", None)
        if used and used != client.model:  # a hosted model was unavailable and a local one answered
            self.last_model = used
            self.last_model_source = (f"{source}; {client.model} unavailable, fell back to the "
                                      f"{tier} default ({getattr(client, 'fallback_reason', '')[:80]})")
        return reply

    def _output(self, phase: str, reply: dict[str, Any]) -> RoleOutput:
        return RoleOutput(
            role=self.role.id,
            phase=phase,
            summary=str(reply.get("summary") or "").strip(),
            decisions=_str_list(reply.get("decisions")),
            concerns=_str_list(reply.get("concerns")),
            model=self.last_model, model_source=self.last_model_source, tokens=dict(self.last_tokens),
        )

    def _failed(self, phase: str, exc: Exception) -> RoleOutput:
        return RoleOutput(role=self.role.id, phase=phase, summary="", error=str(exc),
                          model=self.last_model, model_source=self.last_model_source)

    # --------------------------------------------------------------- actions
    def _do_consult(self, request: CodingRequest, board: Blackboard) -> RoleOutput:
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

    def _do_coordinate(
        self, request: CodingRequest, board: Blackboard, builders: list[RoleSpec], changes: ChangeSet,
        elsewhere: list[RoleSpec] | None = None,
    ) -> RoleOutput:
        """Division lead: split the division's work into owned work items."""
        roster = "\n".join(f"- {b.id}: {b.name} ({b.responsibility})" for b in builders)
        produced = "\n".join(f"- {p}" for p in changes.paths()) or "(none yet)"
        sections = [
            f"## Team notes so far\n{board.render()}",
            f"## Files other divisions already produced this run\n{produced}",
            f"## Specialists you can assign (owner_role must be one of these ids)\n{roster}",
        ]
        if elsewhere:
            def division_name(role: RoleSpec) -> str:
                division = self.tree.division_of(role)
                return division.name if division else "-"
            others = "\n".join(f"- {r.name} ({division_name(r)} division): {r.responsibility}" for r in elsewhere)
            sections.append(
                "## Other divisions building on this run\n"
                f"{others}\n\nLeave every part of the request that falls under their responsibilities to them "
                "(for example containers, Dockerfiles, deployment and CI to DevOps): do not create work items or "
                "claim files for it. Plan only your division's part.")
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

    def _do_build(
        self, request: CodingRequest, board: Blackboard, item: WorkItem, all_items: list[WorkItem],
        changes: ChangeSet,
    ) -> RoleOutput:
        """Write the files for one work item."""
        others = "\n".join(
            f"- {w.id} ({self.tree.by_id[w.owner_role].name if w.owner_role in self.tree.by_id else w.owner_role}): "
            f"{w.title} [{', '.join(w.files)}]"
            for w in all_items if w is not item
        ) or "(none)"
        produced = produced_interfaces(changes, exclude=set(item.files))
        sections = [
            f"## Your work item {item.id}: {item.title}\n{item.description}\n\n"
            f"Files you own: {', '.join(item.files) or 'choose sensible paths'}. Write only these, or new files "
            "no work item owns; changes to files owned by teammates' work items are rejected.",
            f"## Other work items (owned by teammates)\n{others}",
            f"## Files teammates already produced this run (use these exact names when you import them)\n"
            f"{produced}",
            f"## Team decisions\n{board.render()}",
        ]
        query = f"{request.task}\n{item.title}\n{item.description}"
        try:
            reply = self._ask(request, sections, query=query, focus_files=item.files, reader=changes.current)
        except LLMError as exc:
            return self._failed("build", exc)
        return self._collect_files("build", reply, changes, item.id)

    def _do_repair(
        self, request: CodingRequest, problems: list[dict[str, str]], changes: ChangeSet, board: Blackboard,
    ) -> RoleOutput:
        listing = "\n".join(f"- {p['path']} [{p['check']}]: {p['detail']}" for p in problems)
        files = sorted({p["path"] for p in problems})
        sections = [f"## Problems to fix\n{listing}", f"## Team decisions\n{board.render()}"]
        for path in sorted({p["path"] for p in problems if p.get("check") == "removed-code"}):
            before = changes.workspace.read_text(path)
            if before is not None:
                sections.append(f"## {path} before this run (restore what was dropped, merged with the new code)\n"
                                f"```\n{before[:12000]}\n```")
        try:
            reply = self._ask(request, sections, query=listing, focus_files=files, reader=changes.current)
        except LLMError as exc:
            return self._failed("repair", exc)
        return self._collect_files("repair", reply, changes, "repair")

    def _do_review(
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

        files = [(path, content) for action, path, content in subject if action != "delete"]
        for tool in self._extensions("review"):
            try:
                tool_findings = tool(files)
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

    def plan_test_run(self, request: CodingRequest, changes: ChangeSet) -> tuple[list[dict[str, str]], RoleOutput]:
        """Commands that exercise the program the team wrote (run by core/testrun.py)."""
        entry_points = [p for p in changes.paths()
                        if p.endswith(".py") and "__main__" in (changes.current(p) or "")][:2]
        sections = [
            "## Project files written this run\n" + produced_interfaces(changes, exclude=set(), budget=6000),
            "## Entry points (full content below): " + (", ".join(entry_points) or "none found"),
        ]
        try:
            reply = self._ask(request, sections, query=request.task, focus_files=entry_points,
                              reader=changes.current, schema_override=TEST_PLAN_SCHEMA,
                              instructions=TEST_PLAN_INSTRUCTIONS)
        except LLMError as exc:
            return [], self._failed("test-plan", exc)
        commands = [{"command": str(c.get("command", "")).strip(), "purpose": str(c.get("purpose", "")).strip()}
                    for c in reply.get("commands") or [] if isinstance(c, dict) and str(c.get("command", "")).strip()]
        output = self._output("test-plan", reply)
        output.decisions = [f"{c['command']}  ({c['purpose']})" for c in commands]
        return commands, output

    # ------------------------------------------------- public actions (+ hooks)
    def consult(self, request: CodingRequest, board: Blackboard) -> RoleOutput:
        return self._after(self._do_consult(request, board), "consult")

    def coordinate(self, request: CodingRequest, board: Blackboard, builders: list[RoleSpec],
                   changes: ChangeSet, elsewhere: list[RoleSpec] | None = None) -> RoleOutput:
        return self._after(self._do_coordinate(request, board, builders, changes, elsewhere), "coordinate")

    def build(self, request: CodingRequest, board: Blackboard, item: WorkItem, all_items: list[WorkItem],
              changes: ChangeSet) -> RoleOutput:
        return self._after(self._do_build(request, board, item, all_items, changes), "build")

    def repair(self, request: CodingRequest, problems: list[dict[str, str]], changes: ChangeSet,
               board: Blackboard) -> RoleOutput:
        return self._after(self._do_repair(request, problems, changes, board), "repair")

    def review(self, request: CodingRequest, board: Blackboard, subject: list[tuple[str, str, str]], *,
               limit: int = 6000, label: str = "Proposed changes") -> RoleOutput:
        return self._after(self._do_review(request, board, subject, limit=limit, label=label), "review")


_FENCE = re.compile(r"^```[\w.+-]*\n(.*)\n```\s*$", re.DOTALL)


def _strip_fence(content: str) -> str:
    match = _FENCE.match(content.strip())
    text = match.group(1) if match else content
    return text if text.endswith("\n") or not text else text + "\n"
