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
  may really have asked for the removal;
- the project-wide checks in checks.py, for problems involving changed files:
  undefined Python names, packages missing from requirements.txt/package.json,
  files index.html loads, Dockerfile problems and frontend/backend API
  mismatches (the last two are soft: they rest on heuristics).
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from . import jsinterface
from .checks import project_problems, root_import_problems, undefined_name_problems
from .models import VerificationResult, WorkItem
from .projectfacts import ProjectView
from .pyinterface import dotted_reference_problems, import_problems, removed_code
from .workspace import ChangeSet

# Problems that warn instead of blocking once repairs have had their chance.
SOFT_CHECKS = frozenset({"removed-code", "api-contract", "docker"})


def project_files(changes: ChangeSet) -> set[str]:
    files = set(changes.workspace.list_files())
    for change in changes.changes():
        if change.action == "delete":
            files.discard(change.path)
        else:
            files.add(change.path)
    return files


def path_checker(files: set[str]):
    """``exists(path)`` for files, and for folders holding Python files when asked as ``"folder/"``."""
    folders = {str(parent) + "/" for f in files if f.endswith(".py")
               for parent in PurePosixPath(f).parents if str(parent) != "."}
    return lambda path: path in files or path in folders


def django_project_problems(paths: list[str], read, files: set[str]) -> list[dict[str, str]]:
    """A Django settings module needs a manage.py to be run at all."""
    if any(PurePosixPath(f).name == "manage.py" for f in files):
        return []
    for path in paths:
        source = read(path) or ""
        if "INSTALLED_APPS" in source and "ROOT_URLCONF" in source:
            # backend/burgershop/settings.py -> backend/manage.py
            parent = PurePosixPath(path).parent.parent
            where = "manage.py" if str(parent) == "." else f"{parent}/manage.py"
            return [{"path": where, "check": "written", "related": path,
                     "detail": f"{path} is a Django settings module, but the project has no manage.py; "
                               f"create {where} (and wsgi.py/asgi.py next to the settings if they are missing)"}]
    return []


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
    exists = path_checker(files)
    changed = {c.path for c in changes.changes()}
    changed_py = sorted(p for p in changed if p.endswith(".py") and p in files)

    # Files written by different specialists, and files from earlier runs, must agree on what they import.
    all_py = sorted(p for p in files if p.endswith(".py"))
    result.problems += import_problems(all_py, changes.current, exists, changed)
    result.problems += dotted_reference_problems(changed_py, changes.current, exists)
    result.problems += django_project_problems(changed_py, changes.current, files)
    result.checks.append("imports between the project's python files resolve")

    js_files = sorted(p for p in files if jsinterface.is_js(p))
    if js_files:
        result.problems += jsinterface.import_problems(js_files, changes.current, exists, changed)
        result.checks.append("relative javascript imports resolve")

    result.problems += removed_code_problems(changes)
    result.checks.append("changes keep the code files already had")

    view = ProjectView(files, changes.current)
    compiled = {p["path"] for p in result.problems if p["check"] == "python-compile"}
    result.problems += project_problems(view, changed, python_paths=[p for p in changed_py if p not in compiled])
    result.checks.append("names are defined, dependencies are listed, Dockerfiles and API calls fit the project")
    result.passed = not result.problems
    return result


def self_check_problems(changes: ChangeSet, paths: list[str]) -> list[dict[str, str]]:
    """Problems a builder can fix in its own files right away: syntax, undefined names, and imports of
    names that existing files do not define. Imports of files not written yet are left out: a teammate
    may still be writing them."""
    problems: list[dict[str, str]] = []
    files = project_files(changes)
    exists = path_checker(files)
    for path in paths:
        content = changes.current(path)
        if content is None:
            continue
        if path.endswith(".py"):
            try:
                compile(content, path, "exec")
            except SyntaxError as exc:
                problems.append({"path": path, "check": "python-compile", "detail": f"line {exc.lineno}: {exc.msg}"})
                continue
        elif path.endswith(".json"):
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                problems.append({"path": path, "check": "json-parse", "detail": f"line {exc.lineno}: {exc.msg}"})
    view = ProjectView(files, changes.current)
    broken = {p["path"] for p in problems}
    python = [p for p in paths if p.endswith(".py") and p not in broken]
    problems += undefined_name_problems(view, python)
    problems += root_import_problems(view, python)
    problems += [p for p in import_problems(python, changes.current, exists, set(paths))
                 if p["path"] in paths and "does not exist" not in p["detail"]]
    js = [p for p in paths if jsinterface.is_js(p)]
    if js:
        problems += [p for p in jsinterface.import_problems(js, changes.current, exists, set(paths))
                     if p["path"] in paths and "no such file" not in p["detail"]]
    return problems
