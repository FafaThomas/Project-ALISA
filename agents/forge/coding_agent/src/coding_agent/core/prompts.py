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
    "throwaway copy (files the program writes persist between commands). Give script paths from the project root "
    "(python backend/manage.py check); a script in a subfolder runs from its own folder. The packages in the "
    "project's requirements.txt are installed, but no database server, network or other service is running: "
    "leave out commands that need one (for Django, use 'manage.py check' and commands that do not touch the "
    "database, not migrate, runserver or test against PostgreSQL). Every command must exit on its own: never "
    "start a server or anything that runs until stopped (runserver, gunicorn, a web app's main script, a "
    "watcher, a worker). Run only entry points: manage.py subcommands for Django, or files that have "
    "if __name__ == '__main__':. Never run a module such as models.py, views.py, serializers.py or urls.py "
    "directly: it fails before its own code runs. Exercise every feature the task asked "
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

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": _STR, "files": _STR_LIST},
    "required": ["answer", "files"],
}

# Any specialist can answer questions (chat); this replaces its kind's instructions for that call.
ASK_INSTRUCTIONS = (
    "The user is asking a question about their project; the question is under 'Task'. Answer it from your "
    "specialty, using the context below, which is read from the workspace. Files listed as 'Assigned file' "
    "are the ones the user has open or selected. Be specific: name the files, functions and settings you can "
    "see. If the context does not show something, say so instead of guessing. Do not change files and do not "
    "write whole files; short code snippets are fine. Write 'answer' in Markdown, and list in 'files' the "
    "workspace-relative paths your answer refers to."
)

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
        "modules, conventions, and which files will likely change. The project brief is read from the code: "
        "trust it over guesses, and report its open issues that matter for this task as concerns. Do not "
        "propose code."
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
        "number of work items small; group closely related files with the same owner, but give each work item "
        "at most 3 files, and put large files such as UI pages in items of their own. In each description, "
        "name the functions and classes the file must provide (with parameters) and which other files import "
        "them, so separately written files fit together. Plan every file the project needs to run, including "
        "framework boilerplate a new project needs (for Django: manage.py, the project package's __init__.py, "
        "wsgi.py and asgi.py, and each app's __init__.py and apps.py). The project brief lists open issues "
        "found in the code and what the last run left open: plan fixes for the ones that block the request or "
        "sit in files you plan to change."
    ),
    "builder": (
        "Implement your assigned work item. Return the COMPLETE content of every file you create or modify "
        "(never a diff or a placeholder such as '...rest unchanged'). Use workspace-relative paths with forward "
        "slashes. Stay consistent with the team's decisions and with files other developers own. When you "
        "modify an existing file, start from its current content and keep every function, class, export and "
        "URL route already there unless the task asks to remove it; add your code alongside. The 'Project "
        "facts' section is read from the code: keep meeting the requirements from earlier runs it lists, call "
        "the backend routes it lists with the methods it lists, import every name you use, and fix the known "
        "problems it lists in your files. When a frontend call and a backend route disagree, fix the side that "
        "breaks those requirements. Write "
        "production-quality code with no TODO stubs."
    ),
    "repairer": (
        "Fix the reported problems. Find the root cause, then return the COMPLETE corrected content of each "
        "file you change. Change only what is needed to fix the problems, and keep meeting the requirements "
        "from earlier runs listed under 'Project facts'."
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
    "same owner, but give each work item at most 3 files: every file is written in full and an answer has a "
    "length limit, so put large files such as UI pages in items of their own. In each description, name the "
    "functions and classes the file must provide (with parameters) and which other files import them, so "
    "separately written files fit together. Do not assign files another division already produced unless "
    "they must change. Plan every file the project needs to run, including framework boilerplate a new "
    "project needs (for Django: manage.py, the project package's __init__.py, wsgi.py and asgi.py, and each "
    "app's __init__.py and apps.py). The project brief lists open issues found in the code and what the last "
    "run left open: plan fixes for the ones in your division's area that block the request or sit in files "
    "you plan to change. If the request has no part for your division, return an empty work_items list."
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
