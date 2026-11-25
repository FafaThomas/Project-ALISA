"""Fixes the team's tools make without a model, before the Debugging Specialist is asked.

Only changes with one right answer are made here; everything else stays a problem for the repair
loop. Every fix is a normal proposed change (author ``forge_tools``), shown in the run's output
and written only when the run's changes are approved.

- requirements.txt: add packages the code imports (or a Dockerfile starts) whose PyPI name is known;
- package.json: create it, or add packages the frontend imports, and the scripts a Vite app needs;
- Django boilerplate: manage.py, wsgi.py and asgi.py when the settings point at them but they are missing;
  manage.py that passes ``sys.argv[1:]`` (Django then ignores the subcommand);
- imports for names a changed file uses but never imports, when exactly one import fits
  (``models`` -> ``from django.db import models``, a class defined in one project module);
- Dockerfile: ``npm ci`` without a lock file -> ``npm install``; copying a Vite build from ``build/``
  instead of ``dist/``.

Migrations are generated separately (testrun.generate_migrations): that needs Django installed.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import checks
from .projectfacts import (
    DEV_JS_PACKAGES, KNOWN_JS_VERSIONS, ProjectView, auto_addable, build_output_dir, dist_for, join, package_json,
    settings_modules, vite_config,
)

TOOL_ROLE = "forge_tools"
TOOL_ITEM = "tools"


@dataclass
class Fix:
    path: str
    content: str
    reason: str


def _with_lines(text: str, lines: list[str]) -> str:
    body = text if not text or text.endswith("\n") else text + "\n"
    return body + "".join(f"{line}\n" for line in lines)


# ------------------------------------------------------------------ requirements.txt
def requirement_fixes(view: ProjectView, changed: set[str] | None) -> list[Fix]:
    fixes = []
    for manifest, packages in sorted(checks.missing_python_packages(view).items()):
        if not manifest.endswith("requirements.txt"):
            continue  # pyproject.toml: leave TOML edits to a specialist
        users = {f for uses in packages.values() for f, _ in uses}
        if changed is not None and manifest not in changed and not users & changed:
            continue
        lines = sorted({dist_for(top) for top in packages if auto_addable(top)}, key=str.lower)
        if not lines:
            continue
        current = view.read(manifest) or ""
        fixes.append(Fix(manifest, _with_lines(current, lines),
                         f"{'added' if current else 'listed'} {', '.join(lines)} (imported by the code)"))
    return fixes


# ------------------------------------------------------------------ package.json
def _js_version(name: str) -> str:
    return KNOWN_JS_VERSIONS.get(name, "latest")


def _is_dev(name: str) -> bool:
    return name in DEV_JS_PACKAGES or name.startswith(("@types/", "eslint-", "@vitejs/"))


def package_json_fixes(view: ProjectView, changed: set[str] | None) -> list[Fix]:
    fixes = []
    for root, packages in sorted(checks.missing_js_packages(view).items()):
        manifest = join(root, "package.json")
        users = {f for uses in packages.values() for f, _ in uses}
        relevant_files = users | {manifest} | set(view.matching("Dockerfile", "Dockerfile.*"))
        if changed is not None and not relevant_files & changed:
            continue
        data = package_json(view, root)
        is_vite = bool(vite_config(view, root)) or "vite" in packages
        creating = data is None
        if creating:
            name = PurePosixPath(root).name if root != "." else "app"
            data = {"name": re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "app", "private": True,
                    "version": "0.1.0"}
            if is_vite:
                data["type"] = "module"
        assert data is not None
        added = []
        for package in sorted(packages):
            section = "devDependencies" if _is_dev(package) else "dependencies"
            data.setdefault(section, {})
            if package not in data[section]:
                data[section][package] = _js_version(package)
                added.append(package)
        if is_vite:
            data.setdefault("devDependencies", {})
            if "vite" not in data.get("dependencies", {}) and "vite" not in data["devDependencies"]:
                data["devDependencies"]["vite"] = _js_version("vite")
                added.append("vite")
            scripts = data.setdefault("scripts", {})
            for key, command in (("dev", "vite"), ("build", "vite build"), ("preview", "vite preview")):
                scripts.setdefault(key, command)
        ordered = {k: data[k] for k in ("name", "private", "version", "type", "scripts", "dependencies",
                                        "devDependencies") if k in data}
        ordered.update({k: v for k, v in data.items() if k not in ordered})
        content = json.dumps(ordered, indent=2) + "\n"
        if not creating and content == (view.read(manifest) or ""):
            continue
        reason = (f"created with {', '.join(added) or 'no dependencies'}" if creating
                  else f"added {', '.join(added)}" if added else "added the Vite scripts")
        fixes.append(Fix(manifest, content, reason))
    for config in view.matching("vite.config.*"):
        folder = str(PurePosixPath(config).parent)
        manifest = join(folder, "package.json")
        data = package_json(view, folder)
        if not data or any(f.path == manifest for f in fixes):
            continue
        scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
        if scripts.get("build") or (changed is not None and manifest not in changed and config not in changed):
            continue
        data["scripts"] = {**{"dev": "vite", "build": "vite build", "preview": "vite preview"}, **scripts}
        fixes.append(Fix(manifest, json.dumps(data, indent=2) + "\n", "added the Vite build scripts"))
    return fixes


# ------------------------------------------------------------------ Django boilerplate
MANAGE_PY = '''#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{settings}")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and available on your PYTHONPATH "
            "environment variable? Did you forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
'''

SERVER_ENTRY = '''"""{kind} config for the {package} project. It exposes the {kind} callable as a module-level variable named
``{attribute}``."""

import os

from django.core.{lower} import get_{lower}_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{settings}")

{attribute} = get_{lower}_application()
'''


def _setting(tree: ast.Module, name: str) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets) \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def django_fixes(view: ProjectView, changed: set[str] | None) -> list[Fix]:
    fixes: list[Fix] = []
    for settings in settings_modules(view):
        tree = view.tree(settings)
        if tree is None:
            continue
        package_dir = PurePosixPath(settings).parent           # backend/burgershop
        root = str(package_dir.parent)                          # backend
        relative = PurePosixPath(settings).relative_to(PurePosixPath(root)) if root != "." else PurePosixPath(settings)
        module = ".".join(relative.with_suffix("").parts)       # burgershop.settings
        if changed is not None and settings not in changed and not any(
                f.startswith(join(root, "")) for f in changed if f.endswith(".py")):
            continue
        if not view.named("manage.py"):
            fixes.append(Fix(join(root, "manage.py"), MANAGE_PY.replace("{settings}", module),
                             f"created: {settings} is a Django settings module but the project had no manage.py"))
        for setting, kind in (("WSGI_APPLICATION", "WSGI"), ("ASGI_APPLICATION", "ASGI")):
            value = _setting(tree, setting)
            if not value or "." not in value:
                continue
            target_module, attribute = value.rsplit(".", 1)
            target = join(root, target_module.replace(".", "/") + ".py")
            if target in view.files or join(root, target_module.replace(".", "/") + "/__init__.py") in view.files:
                continue
            if not view.has_dir(str(PurePosixPath(target).parent)) and str(PurePosixPath(target).parent) != root:
                continue
            fixes.append(Fix(target, SERVER_ENTRY.format(kind=kind, lower=kind.lower(), package=package_dir.name,
                                                         attribute=attribute, settings=module),
                             f"created: {settings} sets {setting} = '{value}'"))
    for manage in view.named("manage.py"):
        source = view.read(manage) or ""
        fixed = re.sub(r"execute_from_command_line\(\s*sys\.argv\[1:\]\s*\)", "execute_from_command_line(sys.argv)",
                       source)
        if fixed != source:
            fixes.append(Fix(manage, fixed, "passes sys.argv to execute_from_command_line (with sys.argv[1:] "
                                            "Django ignores the subcommand)"))
    return fixes


# ------------------------------------------------------------------ missing imports
def _insert_imports(source: str, statements: list[str]) -> str:
    tree = ast.parse(source)
    insert_at = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            insert_at = node.end_lineno or node.lineno
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and node is tree.body[0]:
            insert_at = node.end_lineno or node.lineno
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            break
    lines = source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    block = "".join(f"{s}\n" for s in statements)
    return "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])


def import_fixes(view: ProjectView, paths: list[str]) -> list[Fix]:
    fixes = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        source = view.read(path)
        if source is None:
            continue
        reasons = []
        prefixed = checks.root_prefixed_imports(view, path)
        if prefixed:
            lines = source.splitlines(keepends=True)
            for line, written, inner in prefixed:
                index = line - 1
                replaced = re.sub(rf"\b{re.escape(written)}\b", inner, lines[index], count=1)
                if replaced != lines[index]:
                    lines[index] = replaced
                    reasons.append(f"{written} -> {inner} (the code runs from {checks.python_root(view, path)}/)")
            source = "".join(lines)
            view._text[path] = source
            view._trees.pop(path, None)
        statements = []
        for name, _, as_module in checks.undefined_names(source, path):
            statement = checks.import_for(view, path, name, as_module)
            if statement and statement not in statements:
                statements.append(statement)
        if statements:
            try:
                source = _insert_imports(source, statements)
            except SyntaxError:
                continue
            reasons.append("added " + "; ".join(f"`{s}`" for s in statements))
        if reasons:
            fixes.append(Fix(path, source, "; ".join(reasons)))
    return fixes


# ------------------------------------------------------------------ Dockerfile
def dockerfile_fixes(view: ProjectView, changed: set[str] | None) -> list[Fix]:
    fixes = []
    for dockerfile in view.matching("Dockerfile", "Dockerfile.*", "*.dockerfile"):
        text = view.read(dockerfile) or ""
        lines = text.splitlines(keepends=True)
        reasons = []
        context = str(PurePosixPath(dockerfile).parent)
        stages = checks._stages(checks.parse_dockerfile(text))
        build_dirs: dict[str, tuple[str, str]] = {}
        for stage in stages:
            for ins in stage.instructions:
                if ins.cmd != "RUN":
                    continue
                folder = checks._project_folder_for(stage, context, view, ins.line)
                if folder is None:
                    continue
                if re.search(r"\bnpm\s+ci\b", ins.args) and join(folder, "package-lock.json") not in view.files:
                    index = ins.line - 1
                    while index < len(lines):
                        replaced = re.sub(r"\bnpm\s+ci\b", "npm install", lines[index])
                        if replaced != lines[index]:
                            lines[index] = replaced
                            reasons.append(f"line {index + 1}: `npm ci` -> `npm install` (no package-lock.json)")
                            break
                        index += 1
                if re.search(r"\b(?:npm\s+run|yarn|pnpm(?:\s+run)?)\s+build\b", ins.args):
                    output = build_output_dir(view, folder)
                    if output:
                        build_dirs[stage.name] = (checks._workdir_at(stage, ins.line), output)
        for stage in stages:
            for copy in checks._copies(stage):
                if copy.stage not in build_dirs:
                    continue
                workdir, output = build_dirs[copy.stage]
                for source in copy.sources:
                    path = checks._abs(workdir, source)
                    prefix = workdir.rstrip("/") + "/"
                    if not path.startswith(prefix):
                        continue
                    first = path[len(prefix):].split("/")[0]
                    if first in ("dist", "build", "out") and first != output:
                        index = copy.line - 1
                        new_source = source.replace(f"/{first}", f"/{output}", 1) if f"/{first}" in source \
                            else source.replace(first, output, 1)
                        while index < len(lines) and source not in lines[index]:
                            index += 1
                        if index < len(lines):
                            lines[index] = lines[index].replace(source, new_source, 1)
                            reasons.append(f"line {index + 1}: copy {new_source} (the build writes to {output}/)")
        content = "".join(lines)
        if reasons and content != text and (changed is None or dockerfile in changed):
            fixes.append(Fix(dockerfile, content, "; ".join(reasons)))
    return fixes


# ------------------------------------------------------------------ all
def deterministic_fixes(view: ProjectView, changed: set[str] | None, *, import_paths: list[str] | None = None
                        ) -> list[Fix]:
    """Every fix above, at most one per file (applied in order, later ones see earlier ones)."""
    fixes: list[Fix] = []
    produced: dict[str, Fix] = {}

    def take(new: list[Fix]) -> None:
        for fix in new:
            if fix.path in produced:
                produced[fix.path].content = fix.content
                produced[fix.path].reason += f"; {fix.reason}"
            else:
                produced[fix.path] = fix
                fixes.append(fix)
            view.files.add(fix.path)
            view._text[fix.path] = fix.content
            view._trees.pop(fix.path, None)

    take(django_fixes(view, changed))
    take(requirement_fixes(view, changed))
    take(package_json_fixes(view, changed))
    take(dockerfile_fixes(view, changed))
    paths = import_paths if import_paths is not None else sorted(
        f for f in view.files if f.endswith(".py") and (changed is None or f in changed))
    take(import_fixes(view, paths))
    return fixes
