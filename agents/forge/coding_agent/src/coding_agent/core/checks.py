"""Project-wide static checks: problems the files gate reports, the brief lists as open issues, and the
automatic fixes (autofix.py) act on. Nothing is executed.

- undefined names: a Python name used but never defined or imported (``models.Prefetch`` with no
  ``from django.db import models``);
- dependencies: packages the code imports (or a Dockerfile starts, such as gunicorn) that
  requirements.txt / package.json do not list, or a missing package.json;
- docker: COPY sources that do not exist, a build stage copied from the wrong output folder,
  ``npm ci`` without a lock file, ``npm run`` scripts that do not exist, and commands that run
  ``manage.py`` or a WSGI module from a folder where the image does not have it;
- html: files index.html loads that do not exist;
- api contract: frontend API calls with no matching backend route, or a method the route does
  not allow. This one is *soft* (it warns once repairs are done): the URL parsing is heuristic.

Each problem is a dict ``{"path", "check", "detail"}`` (plus ``"related"`` naming another file
the fix may need), like the rest of the gate.
"""

from __future__ import annotations

import ast
import builtins
import difflib
import fnmatch
import json
import posixpath
import re
import shlex
import symtable
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import jsinterface
from .projectfacts import (
    IGNORED_PARTS, ProjectView, ancestors, auto_addable, build_output_dir, dist_for, django_routes,
    frontend_calls, join, js_packages, js_root, manifest_names, package_deps, package_json, pip_installs_in_docker,
    python_manifest, satisfies, server_tool_uses, settings_imports, third_party_imports, top_level_definitions,
    vite_config,
)

Problem = dict

# ------------------------------------------------------------------ undefined names
_IMPLICIT = frozenset(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__spec__", "__loader__", "__package__", "__builtins__", "__path__",
    "__annotations__", "__dict__", "__module__", "__qualname__", "__class__", "__debug__", "__cached__",
    "WindowsError",
}
# Names whose import is unambiguous. Module-like names are only imported when used as ``name.attr``.
KNOWN_IMPORTS = {
    "models": "from django.db import models", "transaction": "from django.db import transaction",
    "Q": "from django.db.models import Q", "F": "from django.db.models import F",
    "Prefetch": "from django.db.models import Prefetch", "Sum": "from django.db.models import Sum",
    "Count": "from django.db.models import Count", "settings": "from django.conf import settings",
    "timezone": "from django.utils import timezone", "get_object_or_404": "from django.shortcuts import get_object_or_404",
    "render": "from django.shortcuts import render", "redirect": "from django.shortcuts import redirect",
    "HttpResponse": "from django.http import HttpResponse", "JsonResponse": "from django.http import JsonResponse",
    "Http404": "from django.http import Http404", "path": "from django.urls import path",
    "include": "from django.urls import include", "re_path": "from django.urls import re_path",
    "admin": "from django.contrib import admin", "BaseCommand": "from django.core.management.base import BaseCommand",
    "CommandError": "from django.core.management.base import CommandError",
    "status": "from rest_framework import status", "Response": "from rest_framework.response import Response",
    "serializers": "from rest_framework import serializers", "viewsets": "from rest_framework import viewsets",
    "permissions": "from rest_framework import permissions", "generics": "from rest_framework import generics",
    "mixins": "from rest_framework import mixins", "action": "from rest_framework.decorators import action",
    "api_view": "from rest_framework.decorators import api_view", "APIView": "from rest_framework.views import APIView",
    "DefaultRouter": "from rest_framework.routers import DefaultRouter",
    "Decimal": "from decimal import Decimal", "Path": "from pathlib import Path",
    "dataclass": "from dataclasses import dataclass", "defaultdict": "from collections import defaultdict",
    "Counter": "from collections import Counter", "namedtuple": "from collections import namedtuple",
    "Enum": "from enum import Enum", "wraps": "from functools import wraps", "partial": "from functools import partial",
    "lru_cache": "from functools import lru_cache", "contextmanager": "from contextlib import contextmanager",
    "timedelta": "from datetime import timedelta", "Optional": "from typing import Optional",
    "List": "from typing import List", "Dict": "from typing import Dict", "Any": "from typing import Any",
    "Union": "from typing import Union", "Tuple": "from typing import Tuple", "Callable": "from typing import Callable",
    "Iterable": "from typing import Iterable",
    **{name: f"import {name}" for name in (
        "os", "sys", "json", "re", "math", "time", "random", "logging", "uuid", "subprocess", "shutil", "tempfile",
        "hashlib", "base64", "itertools", "functools", "asyncio", "argparse", "csv", "sqlite3", "socket",
        "threading", "copy", "string", "textwrap", "traceback", "inspect", "secrets", "decimal", "datetime")},
}
_MODULE_LIKE = frozenset({
    "models", "transaction", "settings", "timezone", "admin", "status", "serializers", "viewsets", "permissions",
    "generics", "mixins", "os", "sys", "json", "re", "math", "time", "random", "logging", "uuid", "subprocess",
    "shutil", "tempfile", "hashlib", "base64", "itertools", "functools", "asyncio", "argparse", "csv", "sqlite3",
    "socket", "threading", "copy", "string", "textwrap", "traceback", "inspect", "secrets", "decimal", "datetime",
})


def undefined_names(source: str, filename: str = "<file>") -> list[tuple[str, int, bool]]:
    """(name, first line, used as ``name.attr``) for names used but never bound or imported."""
    try:
        tree = ast.parse(source)
        table = symtable.symtable(source, filename, "exec")
    except (SyntaxError, ValueError):
        return []
    if "globals()" in source or "exec(" in source or "__getattr__" in source:
        return []
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree)):
        return []

    bound: set[str] = set()
    candidates: set[str] = set()

    def visit_bound(t: symtable.SymbolTable) -> None:
        for symbol in t.get_symbols():
            if t.get_type() == "module" and (symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()):
                bound.add(symbol.get_name())
            if symbol.is_declared_global() and (symbol.is_assigned() or symbol.is_imported()):
                bound.add(symbol.get_name())
        for child in t.get_children():
            visit_bound(child)

    def visit_used(t: symtable.SymbolTable) -> None:
        for symbol in t.get_symbols():
            if not symbol.is_referenced() or symbol.is_assigned() or symbol.is_imported() \
                    or symbol.is_parameter() or symbol.is_namespace() or symbol.is_free():
                continue
            if t.get_type() != "module" and not symbol.is_global():
                continue
            candidates.add(symbol.get_name())
        for child in t.get_children():
            visit_used(child)

    visit_bound(table)
    visit_used(table)
    candidates -= bound | _IMPLICIT
    if not candidates:
        return []
    deferred = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                   and any(a.name == "annotations" for a in n.names) for n in tree.body)
    skip: set[int] = set()
    if deferred:  # annotations are never evaluated: a missing name there is not a run-time error
        for node in ast.walk(tree):
            annotations = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                annotations += [a.annotation for a in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                                                        args.vararg, args.kwarg) if a is not None and a.annotation]
                if node.returns is not None:
                    annotations.append(node.returns)
            elif isinstance(node, ast.AnnAssign):
                annotations.append(node.annotation)
            for annotation in annotations:
                skip.update(id(n) for n in ast.walk(annotation))
    first: dict[str, int] = {}
    as_module: dict[str, bool] = {}
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in candidates \
                and id(node) not in skip:
            first[node.id] = min(first.get(node.id, node.lineno), node.lineno)
            parent = parents.get(id(node))
            attribute = isinstance(parent, ast.Attribute) and parent.value is node
            as_module[node.id] = as_module.get(node.id, True) and attribute
    return sorted(((n, line, as_module[n]) for n, line in first.items()), key=lambda item: item[1])


def _bound_names(node: ast.AST) -> set[str]:
    """Names a top-level statement binds in the module (or class) namespace."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
            names.add(sub.name)
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            names.add(sub.id)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in sub.names if a.name != "*"}
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            names.add(sub.name)
    return names


def _import_time_loads(node: ast.AST, deferred: bool) -> list[ast.Name]:
    """Names a statement reads while the module is being imported (not inside function bodies)."""
    loads: list[ast.Name] = []

    def signature(fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> list[ast.AST]:
        parts: list[ast.AST] = [*fn.args.defaults, *[d for d in fn.args.kw_defaults if d is not None]]
        if not isinstance(fn, ast.Lambda):
            parts += fn.decorator_list
            if not deferred:
                args = fn.args
                parts += [a.annotation for a in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg,
                                                 args.kwarg) if a is not None and a.annotation is not None]
                if fn.returns is not None:
                    parts.append(fn.returns)
        return parts

    def visit(sub: ast.AST) -> None:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for part in signature(sub):
                visit(part)
            return
        if isinstance(sub, ast.ClassDef):
            for part in (*sub.decorator_list, *sub.bases, *[k.value for k in sub.keywords]):
                visit(part)
            return  # the class body is checked statement by statement, with its own namespace
        if isinstance(sub, ast.AnnAssign) and deferred:
            if sub.value is not None:
                visit(sub.value)
            return
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            loads.append(sub)
        for child in ast.iter_child_nodes(sub):
            visit(child)

    visit(node)
    return loads


def used_before_definition(source: str) -> list[tuple[str, int, int]]:
    """(name, line used, line defined) for module names read at import time before the line that defines them:
    a class body or decorator that names a class defined further down raises NameError on import."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    deferred = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                   and any(a.name == "annotations" for a in n.names) for n in tree.body)
    first_binding: dict[str, int] = {}
    for stmt in tree.body:
        for name in _bound_names(stmt):
            first_binding.setdefault(name, stmt.lineno)
    found: dict[str, tuple[str, int, int]] = {}

    def check(statements: list[ast.stmt], module_bound: set[str], local: set[str] | None) -> None:
        for stmt in statements:
            for load in _import_time_loads(stmt, deferred):
                name = load.id
                if name in module_bound or (local is not None and name in local) or name in _IMPLICIT:
                    continue
                if name in first_binding and first_binding[name] > load.lineno and name not in found:
                    found[name] = (name, load.lineno, first_binding[name])
            if isinstance(stmt, ast.ClassDef):
                check(stmt.body, module_bound, set())
            if local is None:
                module_bound |= _bound_names(stmt)
            else:
                local |= _bound_names(stmt)

    check(tree.body, set(), None)
    return sorted(found.values(), key=lambda item: item[1])


def python_root(view: ProjectView, path: str) -> str:
    """The folder a Python file's absolute imports start from: the nearest one with manage.py, else the project."""
    for folder in ancestors(path):
        if join(folder, "manage.py") in view.files:
            return folder
    return "."


def import_for(view: ProjectView, path: str, name: str, used_as_module: bool) -> str | None:
    """The import statement that defines ``name`` for ``path``, when there is exactly one sensible answer."""
    defined_in = [f for f in sorted(view.files) if f.endswith(".py") and f != path
                  and name in top_level_definitions(view, f)]
    folder = str(PurePosixPath(path).parent)
    same_package = [f for f in defined_in if str(PurePosixPath(f).parent) == folder]
    if len(same_package) == 1 and PurePosixPath(same_package[0]).name != "__init__.py":
        in_package = join(folder, "__init__.py") in view.files or path.count("/") > python_root(view, path).count("/")
        if in_package:
            return f"from .{PurePosixPath(same_package[0]).stem} import {name}"
    if len(defined_in) == 1:
        root = python_root(view, path)
        target = defined_in[0]
        if root == "." or target.startswith(root.rstrip("/") + "/"):
            relative = target if root == "." else target[len(root.rstrip("/")) + 1:]
            module = relative[:-3].replace("/", ".")
            module = module[: -len(".__init__")] if module.endswith(".__init__") else module
            if module and module != "__init__":
                return f"from {module} import {name}"
    if defined_in:
        return None  # defined in several project modules: ambiguous
    statement = KNOWN_IMPORTS.get(name)
    if statement and (name not in _MODULE_LIKE or used_as_module):
        return statement
    return None


def undefined_name_problems(view: ProjectView, paths: list[str]) -> list[Problem]:
    problems = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        source = view.read(path)
        if source is None:
            continue
        for name, line, as_module in undefined_names(source, path):
            hint = import_for(view, path, name, as_module)
            detail = f"line {line}: '{name}' is used but never defined or imported"
            detail += f" (missing `{hint}`?)" if hint else ""
            problems.append({"path": path, "check": "undefined-names", "detail": detail})
        for name, line, defined in used_before_definition(source):
            problems.append({"path": path, "check": "undefined-names", "detail":
                             f"line {line}: '{name}' is used before it is defined (line {defined}). This line runs "
                             f"when the module is imported, so it raises NameError: define {name} above it, or "
                             "quote the annotation"})
    return problems


def root_prefixed_imports(view: ProjectView, path: str) -> list[tuple[int, str, str]]:
    """(line, written module, module as it must be written) for imports that name the folder the code runs from.

    ``from backend.menu.models import X`` in a Django project under backend/ resolves from the repository
    root, but manage.py puts backend/ itself on the path: there the module is ``menu.models``.
    """
    root = python_root(view, path)
    tree = view.tree(path)
    if root == "." or tree is None:
        return []
    prefix = root.replace("/", ".") + "."
    found = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        elif isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        for module in modules:
            if not module.startswith(prefix):
                continue
            inner = module[len(prefix):]
            stem = join(root, inner.replace(".", "/"))
            if f"{stem}.py" in view.files or f"{stem}/__init__.py" in view.files or view.has_dir(stem):
                found.append((node.lineno, module, inner))
    return found


def root_import_problems(view: ProjectView, paths: list[str]) -> list[Problem]:
    problems = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        root = python_root(view, path)
        for line, written, inner in root_prefixed_imports(view, path):
            problems.append({"path": path, "check": "imports", "detail":
                             f"line {line}: imports {written}, but the code runs from {root}/ (where manage.py is), "
                             f"so that module is {inner}: import it as {inner}"})
    return problems


# ------------------------------------------------------------------ dependencies
def new_python_manifest(view: ProjectView, path: str) -> str:
    root = python_root(view, path)
    return join(root, "requirements.txt")


def missing_python_packages(view: ProjectView) -> dict[str, dict[str, list[tuple[str, int]]]]:
    """manifest path -> import name -> [(file, line)] for packages the manifest does not list."""
    missing: dict[str, dict[str, list[tuple[str, int]]]] = {}
    listed_cache: dict[str, set[str]] = {}
    for path in sorted(f for f in view.files if f.endswith(".py")):
        if any(part in IGNORED_PARTS for part in PurePosixPath(path).parts) or "/migrations/" in f"/{path}":
            continue
        uses = third_party_imports(view, path) + settings_imports(view, path)
        if not uses:
            continue
        manifest = python_manifest(view, path)
        if manifest is not None and manifest not in listed_cache:
            listed_cache[manifest] = manifest_names(view, manifest)
        listed = listed_cache.get(manifest, set()) if manifest else set()
        target = manifest or new_python_manifest(view, path)
        for top, line in uses:
            if not satisfies(top, listed):
                missing.setdefault(target, {}).setdefault(top, []).append((path, line))
    requirements = sorted(f for f in view.files if PurePosixPath(f).name == "requirements.txt")
    docker_installed = pip_installs_in_docker(view)
    for tool, file, line in server_tool_uses(view):
        if not requirements or tool in docker_installed:
            continue
        manifest = requirements[0] if len(requirements) == 1 else next(
            (r for r in requirements if join(str(PurePosixPath(r).parent), "manage.py") in view.files), requirements[0])
        listed = listed_cache.setdefault(manifest, manifest_names(view, manifest))
        if not satisfies(tool, listed):
            missing.setdefault(manifest, {}).setdefault(tool, []).append((file, line))
    return missing


def python_dependency_problems(view: ProjectView, changed: set[str] | None = None) -> list[Problem]:
    problems = []
    for manifest, packages in sorted(missing_python_packages(view).items()):
        users = {f for uses in packages.values() for f, _ in uses}
        if changed is not None and manifest not in changed and not users & changed:
            continue
        listing = "; ".join(
            f"{dist_for(top)} (for '{top}', used in {uses[0][0]} line {uses[0][1]})" for top, uses in sorted(packages.items()))
        exists = manifest in view.files
        detail = (f"{manifest} does not list packages the code needs: {listing}. Add them." if exists else
                  f"the project has no {manifest}, but the code needs: {listing}. Create {manifest} listing them.")
        unknown = [top for top in packages if not auto_addable(top)]
        if unknown:
            detail += (" Check the PyPI names of: " + ", ".join(unknown) + " (the import name is not always the "
                       "package name).")
        problems.append({"path": manifest, "check": "dependencies", "detail": detail,
                         "related": sorted(users)[0]})
    return problems


def missing_js_packages(view: ProjectView) -> dict[str, dict[str, list[tuple[str, int]]]]:
    """frontend folder -> package -> [(file, line)] for packages its package.json does not list."""
    by_root: dict[str, dict[str, list[tuple[str, int]]]] = {}
    for path in sorted(f for f in view.files if jsinterface.is_js(f)):
        if any(part in IGNORED_PARTS for part in PurePosixPath(path).parts):
            continue
        root = js_root(view, path)
        for name, line in js_packages(view, path):
            by_root.setdefault(root, {}).setdefault(name, []).append((path, line))
    missing: dict[str, dict[str, list[tuple[str, int]]]] = {}
    for root, packages in by_root.items():
        data = package_json(view, root)
        if data == {}:
            continue  # does not parse: the JSON check reports it
        listed = package_deps(data) if data is not None else set()
        absent = {name: uses for name, uses in packages.items() if name not in listed}
        if absent or data is None:
            missing[root] = absent
    return missing


def js_dependency_problems(view: ProjectView, changed: set[str] | None = None) -> list[Problem]:
    problems = []
    for root, packages in sorted(missing_js_packages(view).items()):
        manifest = join(root, "package.json")
        users = {f for uses in packages.values() for f, _ in uses}
        if changed is not None and manifest not in changed and not users & changed:
            continue
        listing = ", ".join(f"{name} (used in {uses[0][0]} line {uses[0][1]})" for name, uses in sorted(packages.items()))
        if manifest not in view.files:
            detail = (f"the frontend in {root}/ has no package.json, so `npm install` and `npm run build` fail. Create "
                      f"{manifest} with the scripts the app needs and these dependencies: {listing or 'none yet'}.")
        else:
            detail = f"{manifest} does not list packages the code imports: {listing}. Add them."
        problems.append({"path": manifest, "check": "dependencies", "detail": detail,
                         "related": sorted(users)[0] if users else ""})
    for config in view.matching("vite.config.*"):
        folder = str(PurePosixPath(config).parent)
        data = package_json(view, folder)
        manifest = join(folder, "package.json")
        if data and not ((data.get("scripts") or {}) if isinstance(data.get("scripts"), dict) else {}).get("build"):
            if changed is None or manifest in changed or config in changed:
                problems.append({"path": manifest, "check": "dependencies",
                                 "detail": f"{manifest} has no \"build\" script, so `npm run build` fails; add "
                                           "\"build\": \"vite build\" (and \"dev\": \"vite\")."})
    return problems


# ------------------------------------------------------------------ index.html
_HTML_REF = re.compile(r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"'#?]+)""", re.I)


def html_problems(view: ProjectView, changed: set[str] | None = None) -> list[Problem]:
    problems = []
    for html in view.named("index.html"):
        if changed is not None and html not in changed:
            continue
        folder = str(PurePosixPath(html).parent)
        text = view.read(html) or ""
        for match in _HTML_REF.finditer(text):
            ref = match.group(1).strip()
            if ref.startswith(("http:", "https:", "//", "data:", "mailto:", "%")) or "{" in ref:
                continue
            rel = ref.lstrip("/") if ref.startswith("/") else posixpath.normpath(ref)
            target = posixpath.normpath(join(folder, rel))
            public = posixpath.normpath(join(join(folder, "public"), rel))
            if target in view.files or public in view.files or view.has_dir(target):
                continue
            line = text.count("\n", 0, match.start()) + 1
            problems.append({"path": html, "check": "html", "detail":
                             f"line {line}: loads '{ref}', but {target} does not exist"})
    return problems


# ------------------------------------------------------------------ Dockerfiles
@dataclass
class Instruction:
    cmd: str
    args: str
    line: int


def parse_dockerfile(text: str) -> list[Instruction]:
    instructions: list[Instruction] = []
    buffer, start = "", 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("#") or (not buffer and not stripped):
            continue
        if not buffer:
            start = number
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        buffer += line
        parts = buffer.strip().split(None, 1)
        instructions.append(Instruction(parts[0].upper(), parts[1] if len(parts) > 1 else "", start))
        buffer = ""
    if buffer.strip():
        parts = buffer.strip().split(None, 1)
        instructions.append(Instruction(parts[0].upper(), parts[1] if len(parts) > 1 else "", start))
    return instructions


def _tokens(args: str) -> list[str]:
    text = args.strip()
    if text.startswith("["):
        try:
            value = json.loads(text)
            return [str(v) for v in value] if isinstance(value, list) else []
        except json.JSONDecodeError:
            pass
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def _abs(cwd: str, path: str) -> str:
    return posixpath.normpath(path if path.startswith("/") else posixpath.join(cwd or "/", path))


@dataclass
class _Copy:
    sources: list[str]
    dest: str
    stage: str | None
    line: int
    workdir: str


@dataclass
class _Stage:
    name: str
    instructions: list[Instruction]


def _stages(instructions: list[Instruction]) -> list[_Stage]:
    stages: list[_Stage] = []
    for ins in instructions:
        if ins.cmd == "FROM":
            words = [w for w in ins.args.split() if not w.startswith("--")]
            name = words[2] if len(words) >= 3 and words[1].lower() == "as" else str(len(stages))
            stages.append(_Stage(name, []))
        elif stages:
            stages[-1].instructions.append(ins)
    return stages


def _copies(stage: _Stage) -> list[_Copy]:
    out, workdir = [], "/"
    for ins in stage.instructions:
        if ins.cmd == "WORKDIR":
            workdir = _abs(workdir, ins.args.strip().strip("\"'"))
        elif ins.cmd in ("COPY", "ADD"):
            words = _tokens(ins.args)
            source_stage = next((w.split("=", 1)[1] for w in words if w.startswith("--from=")), None)
            rest = [w for w in words if not w.startswith("--")]
            if len(rest) >= 2:
                out.append(_Copy(rest[:-1], rest[-1], source_stage, ins.line, workdir))
    return out


def _context_path(context: str, source: str) -> str:
    rel = posixpath.normpath(join(context, source.lstrip("/") if source.startswith("/") else source))
    return "" if rel in (".", "") else rel


def _workdir_at(stage: _Stage, line: int) -> str:
    workdir = "/"
    for ins in stage.instructions:
        if ins.line > line:
            break
        if ins.cmd == "WORKDIR":
            workdir = _abs(workdir, ins.args.strip().strip("\"'"))
    return workdir


def _project_folder_for(stage: _Stage, context: str, view: ProjectView, line: int) -> str | None:
    """The project folder a stage's working directory holds at ``line`` (``COPY frontend/ .``)."""
    workdir = _workdir_at(stage, line)
    folder = None
    for copy in _copies(stage):
        if copy.line > line or copy.stage is not None:
            continue
        dest = _abs(copy.workdir, copy.dest)
        for source in copy.sources:
            rel = _context_path(context, source)
            is_dir = rel == "" or view.has_dir(rel)
            if is_dir and dest == workdir:
                folder = rel or "."
    if folder is None:
        for copy in _copies(stage):
            if copy.line <= line and copy.stage is None and _abs(copy.workdir, copy.dest) == workdir:
                for source in copy.sources:
                    rel = _context_path(context, source)
                    if PurePosixPath(rel).name.startswith("package") and rel.endswith(".json"):
                        folder = str(PurePosixPath(rel).parent)
    return folder


class _Image:
    """Which project files end up where in the final image."""

    def __init__(self, view: ProjectView, stage: _Stage, context: str) -> None:
        self.files: dict[str, str] = {}
        self.workdir = "/"
        self.python_paths: list[str] = []
        self.commands: list[tuple[str, list[str], int, bool]] = []   # (kind, tokens, line, exec form)
        for ins in stage.instructions:
            if ins.cmd == "WORKDIR":
                self.workdir = _abs(self.workdir, ins.args.strip().strip("\"'"))
            elif ins.cmd == "ENV":
                found = re.search(r"PYTHONPATH[= ]\s*([^\s]+)", ins.args)
                if found:
                    self.python_paths += [p for p in found.group(1).strip("\"'").split(":") if p and "$" not in p]
            elif ins.cmd in ("CMD", "ENTRYPOINT"):
                self.commands = [c for c in self.commands if c[0] != ins.cmd]
                self.commands.append((ins.cmd, _tokens(ins.args), ins.line, ins.args.strip().startswith("[")))
        for copy in _copies(stage):
            if copy.stage is not None:
                continue
            dest = _abs(copy.workdir, copy.dest)
            for source in copy.sources:
                if "$" in source or source.startswith(("http://", "https://")):
                    continue
                rel = _context_path(context, source)
                if rel == "" or view.has_dir(rel):
                    prefix = "" if rel == "" else rel.rstrip("/") + "/"
                    for file in view.files:
                        if file.startswith(prefix):
                            self.files[posixpath.join(dest, file[len(prefix):])] = file
                elif rel in view.files:
                    into_dir = copy.dest.endswith("/") or len(copy.sources) > 1 or copy.dest in (".", "./")
                    self.files[posixpath.join(dest, PurePosixPath(rel).name) if into_dir else dest] = rel

    def where(self, name: str) -> list[str]:
        return sorted(p for p in self.files if PurePosixPath(p).name == name)


_LAUNCHERS = ("python", "python3", "gunicorn", "uvicorn", "daphne", "hypercorn")


def _check_command(image: _Image, command: str, cwd: str, origin: str, line: int,
                   python_paths: list[str]) -> str | None:
    """Why ``command`` cannot work in the image, or None."""
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return None
    while words and (words[0] in ("exec", "nohup") or re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0])):
        words = words[1:]
    if not words:
        return None
    program = PurePosixPath(words[0]).name
    if program not in _LAUNCHERS:
        return None
    if "$" in command:
        return None
    if program in ("python", "python3"):
        args = words[1:]
        while args and args[0].startswith("-") and args[0] != "-m":
            args = args[1:]
        if not args or args[0] == "-m" or args[0] == "-c":
            return None
        target = _abs(cwd, args[0])
        if target in image.files:
            return None
        elsewhere = image.where(PurePosixPath(args[0]).name)
        if not elsewhere and not any(PurePosixPath(f).name == PurePosixPath(args[0]).name for f in image.files.values()):
            return None  # not one of the project's files: nothing to compare with
        found = f" ({PurePosixPath(args[0]).name} is at {', '.join(elsewhere)} in the image)" if elsewhere else \
            f" ({PurePosixPath(args[0]).name} is in the project but not copied into the image)"
        return f"runs `{command.strip()}` in {cwd}, but the image has no {target}{found}"
    app = next((w for w in words[1:] if re.fullmatch(r"[A-Za-z_][\w.]*:[A-Za-z_][\w.()]*", w)), None)
    if app is None:
        return None
    module = app.split(":")[0]
    roots = [cwd]
    for flag in ("--chdir", "--pythonpath", "--app-dir"):
        for index, word in enumerate(words):
            if word == flag and index + 1 < len(words):
                roots.insert(0, _abs(cwd, words[index + 1]))
            elif word.startswith(flag + "="):
                roots.insert(0, _abs(cwd, word.split("=", 1)[1]))
    roots += [_abs("/", p) for p in python_paths]
    stem = module.replace(".", "/")
    if any(posixpath.join(root, f"{stem}.py") in image.files or posixpath.join(root, stem, "__init__.py") in image.files
           for root in roots):
        return None
    leaf = f"{stem.split('/')[-1]}.py"
    elsewhere = [p for p in image.where(leaf) if p.endswith(f"/{stem}.py")]
    if not elsewhere:
        return None
    return (f"runs `{command.strip()}` in {cwd}, but the image has no {posixpath.join(cwd, stem)}.py; it is at "
            f"{', '.join(elsewhere)}. Use --chdir {posixpath.dirname(posixpath.dirname(elsewhere[0]))} or change the "
            "working directory")


def _script_problems(image: _Image, script: str, origin: str, view: ProjectView) -> list[Problem]:
    problems = []
    text = view.read(origin) or ""
    cwd = image.workdir
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for part in re.split(r"&&|\|\||;", stripped):
            part = part.strip()
            if part.startswith("cd "):
                target = part[3:].strip().strip("\"'")
                if target and "$" not in target and target != "-":
                    cwd = _abs(cwd, target)
                continue
            why = _check_command(image, part, cwd, origin, number, image.python_paths)
            if why:
                problems.append({"path": origin, "check": "docker", "detail": f"line {number} {why}"})
    return problems


def _conf_problems(image: _Image, origin: str, view: ProjectView) -> list[Problem]:
    problems = []
    text = view.read(origin) or ""
    if "[program:" not in text:
        return []
    sections = re.split(r"(?m)^\[", text)
    offset = 0
    for section in sections:
        command = re.search(r"(?m)^command\s*=\s*(.+)$", section)
        directory = re.search(r"(?m)^directory\s*=\s*(\S+)", section)
        if command:
            line = text.count("\n", 0, text.find(command.group(0), offset)) + 1
            cwd = directory.group(1) if directory and "%" not in directory.group(1) else image.workdir
            why = _check_command(image, command.group(1), cwd, origin, line, image.python_paths)
            if why:
                problems.append({"path": origin, "check": "docker", "detail": f"line {line} {why}"})
        offset += len(section) + 1
    return problems


def dockerfile_problems(view: ProjectView, changed: set[str] | None = None) -> list[Problem]:
    problems: list[Problem] = []
    for dockerfile in view.matching("Dockerfile", "Dockerfile.*", "*.dockerfile"):
        text = view.read(dockerfile) or ""
        context = str(PurePosixPath(dockerfile).parent)
        stages = _stages(parse_dockerfile(text))
        if not stages:
            continue
        found: list[Problem] = []
        referenced: set[str] = {dockerfile}
        build_dirs: dict[str, tuple[str, str]] = {}   # stage -> (workdir, output folder)
        for stage in stages:
            for copy in _copies(stage):
                if copy.stage is not None:
                    continue
                for source in copy.sources:
                    if "$" in source or source.startswith(("http://", "https://")):
                        continue
                    rel = _context_path(context, source)
                    if rel == "":
                        continue
                    referenced.add(rel)
                    if any(ch in rel for ch in "*?["):
                        if any(fnmatch.fnmatch(f, rel) or fnmatch.fnmatch(f, rel.rstrip("/") + "/*") for f in view.files):
                            continue
                        target = rel.replace("*", "").replace("?", "")
                    else:
                        if rel in view.files or view.has_dir(rel):
                            continue
                        if any(part in IGNORED_PARTS for part in PurePosixPath(rel).parts):
                            continue  # build output or dependencies we do not list
                        target = rel
                    is_file = not source.endswith("/") and bool(PurePosixPath(target).suffix or "." in
                                                                 PurePosixPath(target).name or PurePosixPath(target).name.lower()
                                                                 in ("dockerfile", "procfile", "makefile"))
                    found.append({"path": target if is_file else dockerfile, "check": "docker", "related": dockerfile,
                                  "detail": f"{dockerfile} line {copy.line} copies '{source}', but {target} does not "
                                            f"exist in the project. Create it, or fix the COPY line."})
            for ins in stage.instructions:
                if ins.cmd != "RUN":
                    continue
                folder = None
                if re.search(r"\bnpm\s+ci\b|\bnpm\s+run\b|\byarn\b|\bpnpm\b", ins.args):
                    folder = _project_folder_for(stage, context, view, ins.line)
                if folder is None:
                    continue
                if re.search(r"\bnpm\s+ci\b", ins.args) and join(folder, "package-lock.json") not in view.files:
                    found.append({"path": dockerfile, "check": "docker", "detail":
                                  f"line {ins.line} runs `npm ci`, which needs {join(folder, 'package-lock.json')}, and "
                                  "the project has none. Use `npm install` instead."})
                data = package_json(view, folder)
                scripts = (data or {}).get("scripts") if isinstance((data or {}).get("scripts"), dict) else {}
                for script in re.findall(r"\b(?:npm\s+run|yarn(?:\s+run)?|pnpm(?:\s+run)?)\s+([\w:-]+)", ins.args):
                    if script in ("install", "add", "ci"):
                        continue
                    if data is not None and script not in (scripts or {}):
                        found.append({"path": join(folder, "package.json"), "check": "docker", "related": dockerfile,
                                      "detail": f"{dockerfile} line {ins.line} runs `npm run {script}`, but "
                                                f"{join(folder, 'package.json')} has no \"{script}\" script"})
                    if script == "build":
                        output = build_output_dir(view, folder)
                        if output:
                            build_dirs[stage.name] = (_workdir_at(stage, ins.line), output)
        for stage in stages:
            for copy in _copies(stage):
                if copy.stage not in build_dirs:
                    continue
                workdir, output = build_dirs[copy.stage]
                for source in copy.sources:
                    path = _abs(workdir, source)
                    if not path.startswith(workdir.rstrip("/") + "/"):
                        continue
                    first = path[len(workdir.rstrip("/")) + 1:].split("/")[0]
                    if first in ("dist", "build", "out") and first != output:
                        found.append({"path": dockerfile, "check": "docker", "detail":
                                      f"line {copy.line} copies {source} from stage '{copy.stage}', but that build "
                                      f"writes to {posixpath.join(workdir, output)} ({'Vite' if output == 'dist' else 'the build tool'} "
                                      f"output folder '{output}'), so {path} does not exist"})
        image = _Image(view, stages[-1], context)
        for kind, words, line, exec_form in image.commands:
            if not words:
                continue
            if words[0] in ("sh", "bash", "/bin/sh", "/bin/bash") and len(words) > 1:
                if words[1] == "-c" and len(words) > 2:
                    why = _check_command(image, words[2], image.workdir, dockerfile, line, image.python_paths)
                    if why:
                        found.append({"path": dockerfile, "check": "docker", "detail": f"line {line} {kind} {why}"})
                    continue
                script = words[1]
            else:
                script = words[0]
            target = _abs(image.workdir, script)
            if target in image.files:
                origin = image.files[target]
                referenced.add(origin)
                found += _script_problems(image, script, origin, view)
                continue
            if script.endswith((".sh", ".py")) and any(PurePosixPath(f).name == PurePosixPath(script).name
                                                       for f in view.files):
                found.append({"path": dockerfile, "check": "docker", "detail":
                              f"line {line} {kind} runs {script}, but the image has no {target} (the project's "
                              f"{PurePosixPath(script).name} is not copied there)"})
                continue
            command = " ".join(shlex.quote(w) for w in words) if exec_form else " ".join(words)
            why = _check_command(image, command, image.workdir, dockerfile, line, image.python_paths)
            if why:
                found.append({"path": dockerfile, "check": "docker", "detail": f"line {line} {kind} {why}"})
        for target, origin in image.files.items():
            if origin.endswith((".conf", ".ini")):
                referenced.add(origin)
                found += _conf_problems(image, origin, view)
        if changed is None or referenced & changed:
            problems += found
    return problems


# ------------------------------------------------------------------ API contract
def api_contract_problems(view: ProjectView, changed: set[str] | None = None) -> list[Problem]:
    routes = [r for r in django_routes(view) if not r.catch_all]
    if not routes:
        return []
    backend_files = {r.source for r in routes} | {r.view_source for r in routes if r.view_source}
    backend_changed = changed is None or bool(backend_files & changed)
    problems = []
    for call in frontend_calls(view):
        if not backend_changed and call.file not in changed:  # type: ignore[operator]
            continue
        matches = [r for r in routes if r.matches(call.path)]
        if not matches:
            names = [r.path for r in routes if not r.prefix_only]
            close = difflib.get_close_matches(call.path, names, n=3, cutoff=0.5) or names[:3]
            closest = next((r for r in routes if close and r.path == close[0]), routes[0])
            problems.append({
                "path": call.file, "check": "api-contract", "related": closest.source,
                "detail": f"line {call.line}: {call.method} {call.path} matches no backend route. Closest backend "
                          f"routes: {', '.join(close)}. Fix the URL here, or add the route to the backend "
                          f"({closest.source}).",
            })
            continue
        if call.method == "ANY" or any(r.methods is None or call.method in r.methods for r in matches):
            continue
        route = matches[0]
        allowed = ", ".join(sorted(set().union(*(r.methods or set() for r in matches)))) or "nothing"
        problems.append({
            "path": call.file, "check": "api-contract", "related": route.view_source or route.source,
            "detail": f"line {call.line}: {call.method} {call.path}, but the backend route {route.path} ({route.view}) "
                      f"only allows {allowed}. Use an allowed method, or add the handler to the view "
                      f"({route.view_source or route.source}).",
        })
    return problems


# ------------------------------------------------------------------ everything
def project_problems(view: ProjectView, changed: set[str] | None = None, *, python_paths: list[str] | None = None
                     ) -> list[Problem]:
    """All the checks above. With ``changed``, only problems involving changed files."""
    paths = python_paths if python_paths is not None else sorted(
        f for f in view.files if f.endswith(".py") and (changed is None or f in changed)
        and not any(part in IGNORED_PARTS for part in PurePosixPath(f).parts))
    problems = undefined_name_problems(view, paths)
    problems += root_import_problems(view, paths)
    problems += python_dependency_problems(view, changed)
    problems += js_dependency_problems(view, changed)
    problems += html_problems(view, changed)
    problems += dockerfile_problems(view, changed)
    problems += api_contract_problems(view, changed)
    return problems
