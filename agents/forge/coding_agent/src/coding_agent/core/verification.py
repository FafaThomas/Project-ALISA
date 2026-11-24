"""The default "files" gate: every assigned file exists, parses, and fits with the others.

This gate never executes generated code. Functional testing stays with the
user; reviewer findings are advisory and reported separately.
"""

from __future__ import annotations

import json

from .models import VerificationResult, WorkItem
from .pyinterface import import_problems
from .workspace import ChangeSet


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

    # Files written by different specialists must agree on names they import from each other.
    changed_py = [c.path for c in changes.changes() if c.action != "delete" and c.path.endswith(".py")]
    result.problems += import_problems(changed_py, changes.current)
    result.checks.append("imports between the project's python files resolve")
    result.passed = not result.problems
    return result
