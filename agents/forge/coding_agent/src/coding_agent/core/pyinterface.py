"""Static facts about Python files in a run: what each module defines, and whether imports between
the project's own modules line up. Nothing is executed."""

from __future__ import annotations

import ast
from pathlib import PurePosixPath
from typing import Callable

Reader = Callable[[str], "str | None"]


def _parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def top_level_names(tree: ast.Module) -> set[str]:
    """Names a module defines at top level (what ``from module import name`` can find)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names if a.name != "*")
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            inner = ast.Module(body=[n for n in ast.walk(node) if isinstance(n, ast.stmt) and n is not node],
                               type_ignores=[])
            names |= top_level_names(inner)
    return names


def is_dynamic(tree: ast.Module) -> bool:
    """A module whose names can't be known statically (star imports, module __getattr__)."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return True
        if isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
            return True
    return False


def signatures(source: str, limit: int = 40) -> list[str]:
    """One line per top-level function/class (classes with their public methods)."""
    tree = _parse(source)
    if tree is None:
        return ["(does not parse yet)"]

    def sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        prefix = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
        returns = f" -> {ast.unparse(fn.returns)}" if fn.returns else ""
        return f"{prefix} {fn.name}({ast.unparse(fn.args)}){returns}"

    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(sig(node))
        elif isinstance(node, ast.ClassDef):
            bases = f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else ""
            lines.append(f"class {node.name}{bases}:")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                        (not item.name.startswith("_") or item.name == "__init__"):
                    lines.append(f"    {sig(item)}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and len(lines) < limit:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    lines.append(f"{target.id} = ...")
    return lines[:limit] or ["(no top-level functions or classes)"]


def _bases(importer: str, level: int) -> list[PurePosixPath]:
    """Folders an import is resolved from, nearest first.

    Relative imports start from the importer's package. Absolute imports are tried from the
    importer's folder and each folder above it: projects are often run from a subfolder
    (``backend/manage.py`` puts ``backend/`` on the path), so ``from menu.models import X`` in
    ``backend/orders/views.py`` means ``backend/menu/models.py``.
    """
    importer_dir = PurePosixPath(importer).parent
    if level:
        base = importer_dir
        for _ in range(level - 1):
            base = base.parent
        return [base]
    bases = [importer_dir, *importer_dir.parents]
    return bases if bases[-1] == PurePosixPath(".") else [*bases, PurePosixPath(".")]


def _clean(path: PurePosixPath) -> str:
    return str(path).lstrip("./") if str(path).startswith("./") else str(path)


def _candidates(module: str, importer: str, level: int) -> list[str]:
    """Workspace paths a module name could refer to, nearest first."""
    parts = module.split(".") if module else []
    out = []
    for base in _bases(importer, level):
        stem = base.joinpath(*parts) if parts else base
        out += [_clean(PurePosixPath(f"{stem}.py")), _clean(PurePosixPath(f"{stem}/__init__.py"))]
    return out


def _project_root_of(module: str, importer: str, exists: Callable[[str], bool]) -> PurePosixPath | None:
    """The folder from which ``module``'s first segment is a project package or module, if any."""
    first = module.split(".")[0]
    for base in _bases(importer, 0):
        # a module, a package, or a folder of modules without __init__.py
        if exists(_clean(base / f"{first}.py")) or exists(_clean(base / first / "__init__.py")) \
                or exists(_clean(base / first) + "/"):
            return base
    return None


def missing_module(module: str, importer: str, level: int, exists: Callable[[str], bool]) -> str | None:
    """A description of the missing file when ``module`` is clearly meant to be a project module."""
    if any(exists(c) for c in _candidates(module, importer, level)):
        return None
    # A folder of Python files without __init__.py is still importable (a namespace package).
    if module and any(exists(_clean(base.joinpath(*module.split("."))) + "/") for base in _bases(importer, level)):
        return None
    if level:
        base = _bases(importer, level)[0]
        stem = base.joinpath(*module.split(".")) if module else base
        return f"{_clean(stem)}.py"
    root = _project_root_of(module, importer, exists)
    if root is None:
        return None  # standard library or third-party
    return f"{_clean(root.joinpath(*module.split('.')))}.py"


def import_problems(paths: list[str], read: Reader, exists: Callable[[str], bool] | None = None,
                    changed: set[str] | None = None) -> list[dict[str, str]]:
    """Imports between the project's Python files that cannot work.

    - ``from X import name`` where X is a project module that does not define ``name``;
    - imports of project modules that do not exist (``from .serializers import ...`` with no
      serializers.py, ``import menu.urls`` when the ``menu`` package has no urls.py).

    With ``changed``, only problems where the importer or the imported file changed are reported.
    """
    exists = exists or (lambda p: read(p) is not None)
    problems: list[dict[str, str]] = []
    cache: dict[str, ast.Module | None] = {}

    def module_tree(path: str) -> ast.Module | None:
        if path not in cache:
            source = read(path)
            cache[path] = _parse(source) if source is not None else None
        return cache[path]

    def relevant(path: str, target: str | None) -> bool:
        return changed is None or path in changed or (target is not None and target in changed)

    for path in paths:
        if not path.endswith(".py"):
            continue
        tree = module_tree(path)
        if tree is None:
            continue  # the compile check reports it
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    gone = missing_module(alias.name, path, 0, exists)
                    if gone and relevant(path, None):
                        problems.append({"path": path, "check": "imports", "detail":
                                         f"line {node.lineno}: imports {alias.name}, but {gone} does not exist"})
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            target = next((c for c in _candidates(module, path, node.level) if exists(c)), None)
            if target is None:
                # "from . import x" in a folder without __init__.py is a namespace package: fine.
                gone = missing_module(module, path, node.level, exists) if module else None
                if gone and relevant(path, None):
                    dots = "." * node.level
                    problems.append({"path": path, "check": "imports", "detail":
                                     f"line {node.lineno}: imports from {dots}{module}, but {gone} does not exist"})
                continue
            if target == path or any(a.name == "*" for a in node.names) or not relevant(path, target):
                continue
            target_tree = module_tree(target)
            if target_tree is None or is_dynamic(target_tree):
                continue
            defined = top_level_names(target_tree)
            package_dir = str(PurePosixPath(target).parent) if target.endswith("__init__.py") else None
            for alias in node.names:
                if alias.name in defined:
                    continue
                if package_dir is not None and any(exists(c) for c in (
                        f"{package_dir}/{alias.name}.py", f"{package_dir}/{alias.name}/__init__.py")):
                    continue  # importing a submodule from a package
                available = ", ".join(sorted(n for n in defined if not n.startswith("_"))[:12]) or "nothing public"
                problems.append({
                    "path": path, "check": "imports",
                    "detail": f"line {node.lineno}: imports {alias.name!r} from {target}, which does not define it "
                              f"(it defines: {available})",
                })
    return problems


# ------------------------------------------------------------------ Django string references
_SETTING_MODULES = {"ROOT_URLCONF": False, "WSGI_APPLICATION": True, "ASGI_APPLICATION": True}


def _strings(node: ast.AST) -> list[ast.Constant]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def dotted_reference_problems(paths: list[str], read: Reader, exists: Callable[[str], bool]) -> list[dict[str, str]]:
    """Dotted module paths written as strings, which imports checks cannot see.

    ``include("menu.urls")``, and in settings files ``ROOT_URLCONF``, ``WSGI_APPLICATION``,
    ``INSTALLED_APPS`` and ``MIDDLEWARE``. Only references whose first segment is a project
    package are checked; ``django.contrib.admin`` and other packages are left alone.
    """
    problems: list[dict[str, str]] = []

    def check(path: str, line: int, dotted: str, what: str, attribute: bool, app_config: bool = False) -> None:
        module = dotted.rsplit(".", 1)[0] if attribute and "." in dotted else dotted
        gone = missing_module(module, path, 0, exists)
        if gone and app_config and "." in dotted:
            # INSTALLED_APPS may name an AppConfig class: "menu.apps.MenuConfig"
            parent = dotted.rsplit(".", 1)[0]
            if missing_module(parent, path, 0, exists) is None and _project_root_of(parent, path, exists):
                gone = None
        if gone:
            # Filed under the missing file, so the repair creates it instead of editing the reference
            # (in a real run the Debugging Specialist "fixed" settings.py twice and never wrote wsgi.py).
            problems.append({"path": gone, "check": "references", "related": path,
                             "detail": f"{path} line {line}: {what} '{dotted}', but {gone} does not exist. "
                                       f"Create {gone}, unless the reference itself is wrong."})

    for path in paths:
        if not path.endswith(".py"):
            continue
        source = read(path)
        tree = _parse(source) if source is not None else None
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "include" \
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                check(path, node.lineno, node.args[0].value, "include() names", False)
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for name in names:
                if name in _SETTING_MODULES and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    check(path, node.lineno, node.value.value, name, _SETTING_MODULES[name])
                elif name == "INSTALLED_APPS":
                    for const in _strings(node.value):
                        if const.value.rsplit(".", 1)[-1].endswith("Middleware"):
                            problems.append({"path": path, "check": "references", "detail":
                                             f"line {const.lineno}: INSTALLED_APPS lists '{const.value}', which is a "
                                             "middleware class; it belongs in MIDDLEWARE"})
                        else:
                            check(path, const.lineno, const.value, "INSTALLED_APPS lists", False, app_config=True)
                elif name == "MIDDLEWARE":
                    for const in _strings(node.value):
                        check(path, const.lineno, const.value, "MIDDLEWARE lists", True)
    return problems


# ------------------------------------------------------------------ removed code
_ROUTE_CALLS = {"path", "re_path", "url"}


def _definitions(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_")}


def _routes(tree: ast.Module) -> set[str]:
    routes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in _ROUTE_CALLS \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            routes.add(node.args[0].value)
    return routes


def removed_code(old: str, new: str) -> tuple[set[str], set[str]]:
    """(public top-level functions/classes, URL routes) present in ``old`` but not in ``new``."""
    before, after = _parse(old), _parse(new)
    if before is None or after is None:
        return set(), set()
    return _definitions(before) - _definitions(after), _routes(before) - _routes(after)
