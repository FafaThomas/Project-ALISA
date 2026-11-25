"""The default "files" gate: every assigned file exists, parses, and fits with the others.

The gate itself never executes generated code (the Debugging Specialist's test
run, in testrun.py, does that separately). Reviewer findings are advisory and
reported separately.

Checks:

- every file a work item was assigned was produced, and none is empty;
- Python compiles, JSON parses;
- imports between the project's Python files resolve, including modules
  imported from a parent folder (``backend/`` in a Django project) and dotted
  module paths written as strings (``include("menu.urls")``, settings);
- relative JavaScript/TypeScript imports point at files that export what is
  imported;
- a change to an existing file does not silently drop public functions,
  classes, exports or URL routes. These are *soft* problems: they go to repair
  first, and what remains afterwards is reported as a warning, since the task
  may really have asked for the removal.
"""

from __future__ import annotations

import json

from . import jsinterface
from .models import VerificationResult, WorkItem
from .pyinterface import dotted_reference_problems, import_problems, removed_code
from .workspace import ChangeSet

# Problems that warn instead of blocking once repairs have had their chance.
SOFT_CHECKS = frozenset({"removed-code"})


def project_files(changes: ChangeSet) -> set[str]:
    files = set(changes.workspace.list_files())
    for change in changes.changes():
        if change.action == "delete":
            files.discard(change.path)
        else:
            files.add(change.path)
    return files


def removed_code_problems(changes: ChangeSet) -> list[dict[str, str]]:
    problems = []
    for change in changes.changes():
        if change.action != "modify":
            continue
        old = changes.workspace.read_text(change.path)
        if old is None:
            continue
        lost: list[str] = []
        if change.path.endswith(".py"):
            names, routes = removed_code(old, change.content)
            lost += [f"{n}" for n in sorted(names)]
            lost += [f"route '{r}'" for r in sorted(routes)]
        elif jsinterface.is_js(change.path):
            lost += [f"export {n}" for n in sorted(jsinterface.removed_exports(old, change.content))]
        if lost:
            problems.append({
                "path": change.path, "check": "removed-code",
                "detail": f"this change removes {', '.join(lost)}, which the file had before. Keep existing code "
                          "unless the task asks to remove it: merge the new code into the existing file.",
            })
    return problems


def verify_files(changes: ChangeSet, work_items: list[WorkItem]) -> VerificationResult:
    result = VerificationResult(passed=True)
    expected = {path for item in work_items for path in item.files}
    for path in sorted(expected):
        try:
            normalized = changes.workspace.normalize(path)
        except ValueError:
            continue  # rejected paths are reported when the plan is parsed
        if changes.current(normalized) is None:
            result.problems.append({"path": normalized, "check": "written", "detail": "assigned file was not produced"})
    result.checks.append(f"{len(expected)} assigned file(s) produced")

    for change in changes.changes():
        if change.action == "delete":
            continue
        lower = change.path.lower()
        if not change.content.strip() and not lower.endswith(("__init__.py", ".gitkeep")):
            result.problems.append({"path": change.path, "check": "non-empty", "detail": "file is empty"})
        if lower.endswith(".py"):
            try:
                compile(change.content, change.path, "exec")
            except SyntaxError as exc:
                result.problems.append({
                    "path": change.path, "check": "python-compile",
                    "detail": f"line {exc.lineno}: {exc.msg}",
                })
        elif lower.endswith(".json"):
            try:
                json.loads(change.content)
            except json.JSONDecodeError as exc:
                result.problems.append({
                    "path": change.path, "check": "json-parse", "detail": f"line {exc.lineno}: {exc.msg}",
                })
    result.checks.append("python files compile, json files parse")

    files = project_files(changes)
    exists = files.__contains__
    changed = {c.path for c in changes.changes()}
    changed_py = sorted(p for p in changed if p.endswith(".py") and p in files)

    # Files written by different specialists, and files from earlier runs, must agree on what they import.
    all_py = sorted(p for p in files if p.endswith(".py"))
    result.problems += import_problems(all_py, changes.current, exists, changed)
    result.problems += dotted_reference_problems(changed_py, changes.current, exists)
    result.checks.append("imports between the project's python files resolve")

    js_files = sorted(p for p in files if jsinterface.is_js(p))
    if js_files:
        result.problems += jsinterface.import_problems(js_files, changes.current, exists, changed)
        result.checks.append("relative javascript imports resolve")

    result.problems += removed_code_problems(changes)
    result.checks.append("changes keep the code files already had")
    result.passed = not result.problems
    return result
