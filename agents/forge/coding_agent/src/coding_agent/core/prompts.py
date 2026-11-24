"""System prompts and JSON output schemas for each role kind."""

from __future__ import annotations

from typing import Any

from .registry import RoleSpec, Tree

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}

NOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": _STR, "decisions": _STR_LIST, "concerns": _STR_LIST},
    "required": ["summary", "decisions", "concerns"],
}

WORK_ITEMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": _STR,
        "work_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": _STR, "title": _STR, "owner_role": _STR, "description": _STR, "files": _STR_LIST,
                },
                "required": ["id", "title", "owner_role", "description", "files"],
            },
        },
    },
    "required": ["summary", "work_items"],
}

FILES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": _STR,
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": _STR,
                    "action": {"type": "string", "enum": ["create", "modify", "delete"]},
                    "content": _STR,
                },
                "required": ["path", "action", "content"],
            },
        },
        "notes": _STR_LIST,
    },
    "required": ["summary", "files"],
}

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": _STR,
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"]},
                    "file": _STR, "issue": _STR, "suggestion": _STR,
                },
                "required": ["severity", "issue"],
            },
        },
    },
    "required": ["summary", "findings"],
}

TEST_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": _STR,
        "commands": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"command": _STR, "purpose": _STR},
                "required": ["command", "purpose"],
            },
        },
    },
    "required": ["summary", "commands"],
}

TEST_PLAN_INSTRUCTIONS = (
    "Plan a short test run of the program the team just wrote. List up to 8 shell-free commands, each "
    "'python <project file> <args>' or 'python -m <project module> <args>', run in order from the project root in a "
    "throwaway copy (files the program writes persist between commands). Exercise every feature the task asked "
    "for, the way a user would, including one realistic error case. Commands must finish in a few seconds without "
    "keyboard input or network access. Use only arguments the code actually accepts; read the entry point's "
    "argument parsing first. Use concrete values only, never placeholders such as <id>: if a command needs a "
    "value the program generates at run time (a random ID, for example), leave that command out. Do not write "
    "tests or files yourself."
)

TEAM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"rationale": _STR, "team": _STR_LIST},
    "required": ["rationale", "team"],
}

SCHEMA_BY_KIND: dict[str, dict[str, Any]] = {
    "analyst": NOTES_SCHEMA,
    "planner": NOTES_SCHEMA,
    "designer": NOTES_SCHEMA,
    "coordinator": WORK_ITEMS_SCHEMA,
    "builder": FILES_SCHEMA,
    "repairer": FILES_SCHEMA,
    "reviewer": FINDINGS_SCHEMA,
}

_KIND_INSTRUCTIONS: dict[str, str] = {
    "analyst": (
        "Study the existing workspace. Report what already exists that matters for this task: entry points, "
        "modules, conventions, and which files will likely change. Do not propose code."
    ),
    "planner": (
        "Contribute your perspective to the plan. Record concrete decisions the team should follow and any "
        "concerns. Where the request is ambiguous, choose a sensible default and say so; do not ask questions. "
        "Do not write code."
    ),
    "designer": (
        "Contribute design decisions from your specialty: structure, contracts, names, file layout. Decisions "
        "must be specific enough for developers to implement without guessing. Do not write code."
    ),
    "coordinator": (
        "Turn the team's plan and design into work items. Each work item has one owner from the developers "
        "listed below and an explicit list of workspace-relative file paths it creates or modifies. Each file "
        "belongs to exactly one work item. Order items so that files others depend on come first. Keep the "
        "number of work items small; group closely related files with the same owner. In each description, "
        "name the functions and classes the file must provide (with parameters) and which other files import "
        "them, so separately written files fit together."
    ),
    "builder": (
        "Implement your assigned work item. Return the COMPLETE content of every file you create or modify "
        "(never a diff or a placeholder such as '...rest unchanged'). Use workspace-relative paths with forward "
        "slashes. Stay consistent with the team's decisions and with files other developers own. Write "
        "production-quality code with no TODO stubs."
    ),
    "repairer": (
        "Fix the reported problems. Find the root cause, then return the COMPLETE corrected content of each "
        "file you change. Change only what is needed to fix the problems."
    ),
    "reviewer": (
        "Review the proposed changes from your specialty only. Report concrete findings with the file, the "
        "issue and a suggested fix. Use severity honestly; an empty findings list is a valid answer."
    ),
}


_LEAD_INSTRUCTIONS = (
    "You are acting as the lead of your division. Split your division's part of the request into work items. "
    "Each work item has one owner from the specialists listed below and an explicit list of workspace-relative "
    "file paths it creates or modifies. Each file belongs to exactly one work item. Order items so that files "
    "others depend on come first. Keep the number of work items small; group closely related files with the "
    "same owner. In each description, name the functions and classes the file must provide (with "
    "parameters) and which other files import them, so separately written files fit together. Do not "
    "assign files another division already produced unless they must change."
)


def system_prompt(role: RoleSpec, tree: Tree, *, as_lead: bool = False, instructions: str | None = None) -> str:
    division = tree.division_of(role)
    team = tree.team_of(role)
    where = f"{division.name} division" if division else "coding agent"
    if team is not None:
        where += f", {team.name} team"
    lead_note = _LEAD_INSTRUCTIONS
    if as_lead and role.is_team:
        lead_note = lead_note.replace("lead of your division", "lead of your team")
    parts = [
        f"You are the {role.name} on ALISA Forge's software engineering team "
        f"({where}). Your responsibility: {role.responsibility}.",
        instructions or (lead_note if as_lead else _KIND_INSTRUCTIONS[role.kind]),
    ]
    if role.instructions:
        parts.append(role.instructions)
    parts.append("Respond with one JSON object matching the requested schema. No Markdown fences.")
    return "\n\n".join(parts)


DIRECTOR_SYSTEM = (
    "You are the Engineering Director of ALISA Forge's coding agent, and its lead. Choose the smallest team that "
    "can complete the request well. Every extra specialist costs time, so include a role only when the request "
    "needs its expertise. The team must include at least one developer who writes code. "
    "Respond with one JSON object: {\"rationale\": str, \"team\": [role ids]}."
)
