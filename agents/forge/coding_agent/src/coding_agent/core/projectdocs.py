"""What one run leaves for the next: a changelog in the project and a project brief for the team.

- ``CHANGELOG.md`` (project root): one entry per run that wrote changes, newest first: the task,
  the files created/changed/deleted, what the checks and the test run found, and what was left open.
  Taken from the run report, not written by a model.
- ``.coding-agent/PROJECT.md``: the project brief. Layout, dependencies, backend routes, frontend
  API calls, environment variables, open issues found in the code, and what the last run left open.
  Rewritten after every run that wrote changes. The facts come from the code (projectfacts.py).

At the start of a run the brief is rebuilt from the code as it is then (so hand edits since the
last run count), plus the "left over" notes and recent changelog entries that only the earlier
runs know. Analysts, planners, designers and leads get all of it; builders and the Debugging
Specialist get the facts about the files they work on (see specialist.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import checks
from .models import FileChange, RunReport
from .projectfacts import (
    ProjectView, django_routes, env_vars, frontend_calls, layout, manifest_names, package_deps, package_json,
)
from .workspace import Workspace

BRIEF_FILE = "PROJECT.md"
CHANGELOG_FILE = "CHANGELOG.md"
CHANGELOG_HEADER = ("# Changelog\n\nChanges ALISA Forge's coding agent wrote to this project, newest first. "
                    "Each entry is written by the run itself.\n")
_LEFTOVER_START, _LEFTOVER_END = "<!-- forge:leftover -->", "<!-- /forge:leftover -->"
_REQUIREMENTS_START, _REQUIREMENTS_END = "<!-- forge:requirements -->", "<!-- /forge:requirements -->"
MAX_REQUIREMENTS = 40
_REQUIREMENT_LINE = re.compile(r"^- \[(constraint|acceptance)\] (.*?)(?: \(run ([\w-]+)\))?$")
_ENTRY_MARK = re.compile(r"<!-- forge-run: ([\w-]+) -->")
TOOL_ROLE = "forge_tools"


def workspace_view(workspace: Workspace) -> ProjectView:
    return ProjectView(workspace.list_files(), workspace.read_text)


# ------------------------------------------------------------------ facts
def _limited(lines: list[str], limit: int) -> list[str]:
    return lines if len(lines) <= limit else lines[:limit] + [f"- ... and {len(lines) - limit} more"]


def dependency_lines(view: ProjectView) -> list[str]:
    lines = []
    for manifest in view.named("requirements.txt"):
        listed = [line.split("#")[0].strip() for line in (view.read(manifest) or "").splitlines()]
        listed = [line for line in listed if line and not line.startswith("-")]
        lines.append(f"- {manifest}: {', '.join(listed) or '(empty)'}")
    for manifest in view.named("pyproject.toml"):
        names = manifest_names(view, manifest)
        if names:
            lines.append(f"- {manifest}: {', '.join(sorted(names))}")
    for manifest in view.named("package.json"):
        data = package_json(view, str(PurePosixPath(manifest).parent))
        if data is None:
            continue
        lines.append(f"- {manifest}: {', '.join(sorted(package_deps(data))) or '(no dependencies)'}")
    return lines


def route_lines(view: ProjectView, limit: int = 60) -> list[str]:
    lines = []
    for route in sorted(django_routes(view), key=lambda r: r.path):
        if route.catch_all:
            lines.append(f"- {route.path}  (catch-all: {route.view}, {route.source})")
            continue
        methods = ", ".join(sorted(route.methods)) if route.methods else "any method"
        kind = "everything under it, " if route.prefix_only else ""
        lines.append(f"- {methods} {route.path}  ({kind}{route.view}, {route.source})")
    return _limited(lines, limit)


def call_lines(view: ProjectView, limit: int = 60) -> list[str]:
    return _limited([f"- {c.method} {c.path}  ({c.file} line {c.line})" for c in
                     sorted(frontend_calls(view), key=lambda c: (c.path, c.method))], limit)


def facts_markdown(view: ProjectView) -> str:
    sections = []
    shape = layout(view)
    if shape:
        sections.append("## Layout\n" + "\n".join(f"- {line}" for line in shape))
    deps = dependency_lines(view)
    if deps:
        sections.append("## Dependencies\n" + "\n".join(deps))
    routes = route_lines(view)
    if routes:
        sections.append("## Backend routes\n" + "\n".join(routes))
    calls = call_lines(view)
    if calls:
        sections.append("## Frontend API calls\n" + "\n".join(calls))
    variables = env_vars(view)
    if variables:
        sections.append("## Environment variables the code reads\n" + "\n".join(
            f"- {name} ({', '.join(files[:3])})" for name, files in sorted(variables.items())))
    return "\n\n".join(sections)


def issue_lines(problems: list[dict], limit: int = 40) -> list[str]:
    return _limited([f"- [{p['check']}] {p['path']}: {p['detail'].splitlines()[0]}" for p in problems], limit)


# ------------------------------------------------------------------ reading (start of a run)
def _brief_section(workspace: Workspace, start_mark: str, end_mark: str) -> list[str]:
    try:
        text = (workspace.agent_dir / BRIEF_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    start, end = text.find(start_mark), text.find(end_mark)
    if start == -1 or end == -1:
        return []
    return [line for line in text[start + len(start_mark):end].splitlines() if line.startswith("- ")]


def leftover(workspace: Workspace) -> list[str]:
    """What the last run left open (warnings, failed test commands), from PROJECT.md."""
    return [line for line in _brief_section(workspace, _LEFTOVER_START, _LEFTOVER_END) if line != "- nothing"]


def requirements(workspace: Workspace) -> list[str]:
    """Constraints and acceptance criteria earlier runs were given, from PROJECT.md:
    ``- [acceptance] The API is under /api/ (run <id>)``."""
    return [line for line in _brief_section(workspace, _REQUIREMENTS_START, _REQUIREMENTS_END)
            if _REQUIREMENT_LINE.match(line)]


def merged_requirements(existing: list[str], report: RunReport) -> list[str]:
    """Earlier requirements plus this run's. A requirement given again moves to the end with the newer run id."""
    entries: dict[str, str] = {}
    for line in existing:
        found = _REQUIREMENT_LINE.match(line)
        if found:
            entries.pop(found.group(2).strip().lower(), None)
            entries[found.group(2).strip().lower()] = line
    for kind, values in (("constraint", report.constraints), ("acceptance", report.acceptance_criteria)):
        for value in values:
            text = " ".join(str(value).split())
            if text:
                entries.pop(text.lower(), None)
                entries[text.lower()] = f"- [{kind}] {text} (run {report.run_id})"
    return list(entries.values())[-MAX_REQUIREMENTS:]


def recent_changelog(workspace: Workspace, entries: int = 3) -> str:
    text = workspace.read_text(CHANGELOG_FILE) or ""
    parts = re.split(r"(?m)^(?=## )", text)
    runs = [p.strip() for p in parts if _ENTRY_MARK.search(p)]
    return "\n\n".join(runs[:entries])


@dataclass
class Brief:
    text: str = ""
    open_issues: int = 0
    left_open: int = 0
    history: bool = False
    requirements: int = 0


REQUIREMENTS_TITLE = "Requirements from earlier runs (keep meeting them unless this request changes them)"


def brief(workspace: Workspace, view: ProjectView | None = None) -> Brief:
    """The project brief as context: facts and open issues from the code now, plus what earlier runs left."""
    view = view or workspace_view(workspace)
    if not view.files:
        return Brief()
    parts = []
    required = requirements(workspace)
    if required:
        parts.append(f"## {REQUIREMENTS_TITLE}\n" + "\n".join(required))
    parts.append(facts_markdown(view))
    issues = checks.project_problems(view)
    if issues:
        parts.append("## Open issues found in the code (fix the ones in files you change, or that block the "
                     "request)\n" + "\n".join(issue_lines(issues)))
    left = leftover(workspace)
    if left:
        parts.append("## Left open by the last run\n" + "\n".join(left))
    history = recent_changelog(workspace)
    if history:
        parts.append("## Recent changes (from CHANGELOG.md)\n" + re.sub(r"(?m)^## ", "### ", history))
    return Brief("\n\n".join(p for p in parts if p), len(issues), len(left), bool(history), len(required))


def facts_for_files(view: ProjectView, files: list[str], budget: int = 5000,
                    required: list[str] | None = None) -> str:
    """What a builder or the Debugging Specialist needs to know about the project around ``files``,
    starting with the requirements earlier runs were given (``required``)."""
    from . import jsinterface

    front = any(jsinterface.is_js(f) or f.endswith((".html", ".vue", ".svelte")) for f in files)
    python = any(f.endswith(".py") for f in files)
    infra = any(PurePosixPath(f).name.lower().startswith(("dockerfile", "docker-compose", "compose.", ".dockerignore",
                                                          ".env", "procfile", "supervisord", "entrypoint"))
                or f.endswith((".sh", ".conf", ".ini", ".yml", ".yaml", ".toml")) for f in files)
    sections = []
    if required:
        sections.append(f"### {REQUIREMENTS_TITLE}\n" + "\n".join(required[-20:]))
    if infra or not (front or python):
        shape = layout(view)
        if shape:
            sections.append("### Project layout\n" + "\n".join(f"- {line}" for line in shape))
    if front or infra:
        routes = route_lines(view, 40)
        if routes:
            sections.append("### Backend routes (call these exact paths and methods)\n" + "\n".join(routes))
    if python:
        calls = call_lines(view, 40)
        if calls:
            sections.append("### Frontend API calls (the backend must serve these paths and methods)\n"
                            + "\n".join(calls))
    deps = dependency_lines(view)
    if deps and (front or python or infra):
        sections.append("### Dependencies listed now (add any new package you import to the right file)\n"
                        + "\n".join(deps))
    targets = set(files)
    issues = [p for p in checks.project_problems(view, targets, python_paths=[f for f in files if f.endswith(".py")])
              if p["path"] in targets or p.get("related") in targets]
    if issues:
        sections.append("### Known problems in these files (fix them while you are there)\n"
                        + "\n".join(issue_lines(issues, 20)))
    text = "\n\n".join(sections)
    return text if len(text) <= budget else text[:budget].rsplit("\n", 1)[0] + "\n... [more facts omitted]"


# ------------------------------------------------------------------ writing (end of a run)
def _test_summary(report: RunReport) -> str:
    if not report.test_run:
        return ""
    failed = sum(1 for r in report.test_run if not r.get("skipped") and not r.get("unavailable") and (
        r.get("timed_out") or "Traceback (most recent call last)" in (r.get("stderr") or "") or r.get("reported")))
    na = sum(1 for r in report.test_run if r.get("unavailable"))
    skipped = sum(1 for r in report.test_run if r.get("skipped"))
    parts = [f"{len(report.test_run)} command(s)", f"{failed} failed"]
    if na:
        parts.append(f"{na} needed a service")
    if skipped:
        parts.append(f"{skipped} skipped")
    return "test run: " + ", ".join(parts)


def _gate_summary(report: RunReport) -> str:
    if report.verification is None:
        return ""
    attempts = [int(m.group(1)) for c in report.verification.checks for m in [re.search(r"repair attempt (\d+)", c)] if m]
    state = "passed" if report.verification.passed else "failed"
    return f"files gate {state}" + (f" after {max(attempts)} repair round(s)" if attempts else "")


def leftover_items(report: RunReport) -> list[str]:
    items = [w for w in report.warnings]
    for result in report.test_run:
        if result.get("skipped") or result.get("unavailable"):
            continue
        if result.get("timed_out") or "Traceback (most recent call last)" in (result.get("stderr") or "") \
                or result.get("reported"):
            items.append(f"test command failed: {result.get('ran_as') or result.get('command')}")
    return list(dict.fromkeys(items))


def changelog_entry(report: RunReport, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    task = " ".join(report.task.split())
    task = task if len(task) <= 400 else task[:397] + "..."
    lines = [f"## {when.strftime('%Y-%m-%d %H:%M UTC')} - run {report.run_id}", f"<!-- forge-run: {report.run_id} -->",
             "", f"**Task:** {task}", ""]
    if report.constraints:
        lines += [f"**Constraints:** {'; '.join(report.constraints)}", ""]
    if report.acceptance_criteria:
        lines += [f"**Acceptance criteria:** {'; '.join(report.acceptance_criteria)}", ""]
    groups = {"Created": [], "Changed": [], "Deleted": []}
    for change in report.changes:
        label = {"create": "Created", "modify": "Changed", "delete": "Deleted"}[change.action]
        mark = " (tools)" if change.author_role == TOOL_ROLE else ""
        groups[label].append(f"`{change.path}`{mark}")
    for label, paths in groups.items():
        if paths:
            lines.append(f"- {label}: {', '.join(paths)}")
    checked = ", ".join(s for s in (_gate_summary(report), _test_summary(report)) if s)
    if checked:
        lines.append(f"- Checks: {checked}")
    left = leftover_items(report)
    if left:
        lines.append("- Left open:")
        lines += [f"  - {item}" for item in left[:12]]
    return "\n".join(lines) + "\n"


def updated_changelog(existing: str | None, entry: str) -> str:
    if not existing or not existing.strip():
        return f"{CHANGELOG_HEADER}\n{entry}"
    if existing.startswith("# "):
        # After the title and its intro paragraph, before the first entry.
        first_entry = re.search(r"(?m)^## ", existing)
        if first_entry:
            return existing[:first_entry.start()] + entry + "\n" + existing[first_entry.start():]
        return existing.rstrip("\n") + "\n\n" + entry
    return f"{entry}\n{existing}"


def brief_markdown(view: ProjectView, report: RunReport, when: datetime | None = None,
                   required: list[str] | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    task = " ".join(report.task.split())[:200]
    required = required if required is not None else merged_requirements([], report)
    parts = [
        "# Project brief",
        "_Maintained by ALISA Forge's coding agent and rewritten after every run that writes changes. The facts "
        "are read from the code, not written by a model. Put rules of your own in .coding-agent/memory/CONVENTIONS.md._",
        f"Last run: {report.run_id} on {when.strftime('%Y-%m-%d %H:%M UTC')}: {task}",
        f"{_REQUIREMENTS_START}\n## Requirements from earlier runs\n"
        + ("\n".join(required) if required else "- none recorded yet") + f"\n{_REQUIREMENTS_END}",
        facts_markdown(view),
    ]
    issues = checks.project_problems(view)
    parts.append("## Open issues found in the code\n" + ("\n".join(issue_lines(issues)) if issues else "- none found"))
    left = leftover_items(report)
    parts.append(f"{_LEFTOVER_START}\n## Left open by the last run\n"
                 + ("\n".join(f"- {item}" for item in left) if left else "- nothing") + f"\n{_LEFTOVER_END}")
    return "\n\n".join(p for p in parts if p) + "\n"


def record_run(workspace: Workspace, report: RunReport, run_dir: Path) -> list[str]:
    """After a run's changes were written: add its CHANGELOG.md entry and rewrite the brief. Returns notes."""
    if not report.changes:
        return []
    notes = []
    required = merged_requirements(requirements(workspace), report)
    try:
        existing = workspace.read_text(CHANGELOG_FILE)
        content = updated_changelog(existing, changelog_entry(report))
        workspace.apply([FileChange(CHANGELOG_FILE, "modify" if existing is not None else "create", content,
                                    author_role=TOOL_ROLE, work_item_id="tools")], run_dir)
        notes.append(f"{CHANGELOG_FILE}: added the entry for run {report.run_id}")
    except (OSError, ValueError) as exc:
        notes.append(f"{CHANGELOG_FILE} not updated: {exc}")
    try:
        workspace.agent_dir.mkdir(parents=True, exist_ok=True)
        (workspace.agent_dir / BRIEF_FILE).write_text(
            brief_markdown(workspace_view(workspace), report, required=required), encoding="utf-8")
        notes.append(f".coding-agent/{BRIEF_FILE}: rewritten")
    except OSError as exc:
        notes.append(f".coding-agent/{BRIEF_FILE} not updated: {exc}")
    return notes
