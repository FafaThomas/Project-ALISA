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


def _candidates(module: str, importer: str, level: int) -> list[str]:
    """Workspace paths a module name could refer to, nearest first."""
    parts = module.split(".") if module else []
    importer_dir = PurePosixPath(importer).parent
    if level:
        base = importer_dir
        for _ in range(level - 1):
            base = base.parent
        bases = [base]
    else:
        bases = [importer_dir, PurePosixPath(".")] if str(importer_dir) != "." else [PurePosixPath(".")]
    out = []
    for base in bases:
        stem = base.joinpath(*parts) if parts else base
        out += [f"{stem}.py", f"{stem}/__init__.py"]
    return [str(PurePosixPath(p)).lstrip("./") if p.startswith("./") else str(PurePosixPath(p)) for p in out]


def import_problems(paths: list[str], read: Reader) -> list[dict[str, str]]:
    """``from X import name`` where X is one of the project's modules but does not define ``name``."""
    problems: list[dict[str, str]] = []
    cache: dict[str, ast.Module | None] = {}

    def module_tree(path: str) -> ast.Module | None:
        if path not in cache:
            source = read(path)
            cache[path] = _parse(source) if source is not None else None
        return cache[path]

    for path in paths:
        if not path.endswith(".py"):
            continue
        tree = module_tree(path)
        if tree is None:
            continue  # the compile check reports it
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or any(a.name == "*" for a in node.names):
                continue
            target = next((c for c in _candidates(node.module or "", path, node.level) if read(c) is not None), None)
            if target is None or target == path:
                continue  # standard library, third-party, or not in the project
            target_tree = module_tree(target)
            if target_tree is None or is_dynamic(target_tree):
                continue
            defined = top_level_names(target_tree)
            package_dir = str(PurePosixPath(target).parent) if target.endswith("__init__.py") else None
            for alias in node.names:
                if alias.name in defined:
                    continue
                if package_dir is not None and any(read(c) is not None for c in (
                        f"{package_dir}/{alias.name}.py", f"{package_dir}/{alias.name}/__init__.py")):
                    continue  # importing a submodule from a package
                available = ", ".join(sorted(n for n in defined if not n.startswith("_"))[:12]) or "nothing public"
                problems.append({
                    "path": path, "check": "imports",
                    "detail": f"line {node.lineno}: imports {alias.name!r} from {target}, which does not define it "
                              f"(it defines: {available})",
                })
    return problems
