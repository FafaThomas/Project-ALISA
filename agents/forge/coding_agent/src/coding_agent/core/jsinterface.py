"""Static checks for JavaScript/TypeScript files: relative imports must point at a project file that
exports what is imported. Nothing is executed; package imports (``react``, ``@scope/pkg``) are ignored.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Callable

Reader = Callable[[str], "str | None"]

JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_RESOLVE_SUFFIXES = ("", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
                     "/index.js", "/index.jsx", "/index.ts", "/index.tsx")

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?m)^\s*//.*$")
_IMPORT = re.compile(r"""(?m)^\s*import\s+(?:type\s+)?(?P<clause>[^'";]*?)\s+from\s+['"](?P<src>[^'"]+)['"]""")
_SIDE_EFFECT_IMPORT = re.compile(r"""(?m)^\s*import\s+['"](?P<src>[^'"]+)['"]""")
_REEXPORT = re.compile(r"""(?m)^\s*export\s+(?P<clause>\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+['"](?P<src>[^'"]+)['"]""")
_EXPORT_DECL = re.compile(
    r"(?m)^\s*export\s+(?:declare\s+)?(?:async\s+)?(?:function\*?|const|let|var|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)")
_EXPORT_LIST = re.compile(r"(?m)^\s*export\s*\{([^}]*)\}")
_EXPORT_DESTRUCTURE = re.compile(r"(?m)^\s*export\s+(?:const|let|var)\s*[{\[]")
_MEMBER = r"\b{name}\.([A-Za-z_$][\w$]*)\b"


def is_js(path: str) -> bool:
    return path.lower().endswith(JS_SUFFIXES)


def _strip_comments(source: str) -> str:
    # Block comments keep their line breaks so reported line numbers stay right.
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), source))


def exports(source: str) -> tuple[set[str], bool]:
    """(names a module exports, whether the list is incomplete because of ``export *`` or destructuring)."""
    text = _strip_comments(source)
    names = set(_EXPORT_DECL.findall(text))
    if re.search(r"(?m)^\s*export\s+default\b", text):
        names.add("default")
    for group in _EXPORT_LIST.findall(text):
        for part in group.split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[-1].strip())
    for clause, _ in ((m.group("clause"), m.group("src")) for m in _REEXPORT.finditer(text)):
        if clause.startswith("{"):
            for part in clause.strip("{}").split(","):
                if part.strip():
                    names.add(part.strip().split(" as ")[-1].strip())
    incomplete = bool(re.search(r"(?m)^\s*export\s+\*\s+from", text) or _EXPORT_DESTRUCTURE.search(text)
                      or re.search(r"\bmodule\.exports\b|\bexports\.\w+\s*=", text))
    return names, incomplete


def _parse_clause(clause: str) -> tuple[str | None, list[str], str | None]:
    """``Default, { a, b as c }`` / ``* as ns`` -> (default name, named imports, namespace name)."""
    clause = clause.strip()
    default = namespace = None
    named: list[str] = []
    braces = re.search(r"\{([^}]*)\}", clause)
    if braces:
        for part in braces.group(1).split(","):
            part = part.strip()
            if part.startswith("type "):
                continue
            if part:
                named.append(part.split(" as ")[0].strip())
        clause = (clause[:braces.start()] + clause[braces.end():]).strip()
    ns = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if ns:
        namespace = ns.group(1)
        clause = clause.replace(ns.group(0), "")
    rest = clause.strip().strip(",").strip()
    if rest and re.fullmatch(r"[A-Za-z_$][\w$]*", rest):
        default = rest
    return default, named, namespace


def resolve(importer: str, spec: str, exists: Callable[[str], bool]) -> str | None:
    base = PurePosixPath(importer).parent.joinpath(spec)
    parts: list[str] = []
    for part in base.parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part != ".":
            parts.append(part)
    stem = "/".join(parts)
    for suffix in _RESOLVE_SUFFIXES:
        if exists(stem + suffix):
            return stem + suffix
    return None


def import_problems(paths: list[str], read: Reader, exists: Callable[[str], bool],
                    changed: set[str] | None = None) -> list[dict[str, str]]:
    """Broken relative imports in ``paths``. With ``changed``, only imports where the importer or the
    imported file changed this run are reported (old breakage elsewhere is not this run's job)."""
    problems: list[dict[str, str]] = []
    cache: dict[str, tuple[set[str], bool]] = {}

    def exported(path: str) -> tuple[set[str], bool]:
        if path not in cache:
            source = read(path)
            cache[path] = exports(source) if source is not None else (set(), True)
        return cache[path]

    def report(path: str, detail: str) -> None:
        problems.append({"path": path, "check": "js-imports", "detail": detail})

    for path in paths:
        if not is_js(path):
            continue
        source = read(path)
        if source is None:
            continue
        text = _strip_comments(source)
        found = [(m.group("clause"), m.group("src"), m.start()) for m in _IMPORT.finditer(text)]
        found += [("", m.group("src"), m.start()) for m in _SIDE_EFFECT_IMPORT.finditer(text)]
        found += [(m.group("clause"), m.group("src"), m.start()) for m in _REEXPORT.finditer(text)]
        for clause, spec, offset in found:
            if not spec.startswith("."):
                continue  # a package import, or a bundler alias
            line = text.count("\n", 0, offset) + 1
            target = resolve(path, spec, exists)
            if changed is not None and path not in changed and (target is None or target not in changed):
                continue
            if target is None:
                report(path, f"line {line}: imports '{spec}', but no such file exists in the project")
                continue
            if not is_js(target):
                continue  # CSS, JSON, images: existence is enough
            names, incomplete = exported(target)
            # "export * from" has nothing to check; "import * as ns from" does (its members).
            default, named, namespace = _parse_clause(clause) if clause.strip() != "*" else (None, [], None)
            if clause.startswith("{") and _REEXPORT.match(text, offset):
                named = [p.strip().split(" as ")[0].strip() for p in clause.strip("{}").split(",") if p.strip()]
            available = ", ".join(sorted(n for n in names if n != "default")[:12]) or "no named exports"
            if default and "default" not in names and not incomplete:
                report(path, f"line {line}: default-imports {default!r} from '{spec}', but {target} has no "
                             f"default export (it exports: {available}); use import {{ ... }} instead")
            for name in named:
                if name != "default" and name not in names and not incomplete:
                    report(path, f"line {line}: imports {name!r} from '{spec}', but {target} does not export it "
                                 f"(it exports: {available})")
            for alias in [n for n in (namespace, default if "default" not in names else None) if n]:
                if incomplete:
                    continue
                used = set(re.findall(_MEMBER.format(name=re.escape(alias)), text))
                for member in sorted(used - names):
                    report(path, f"uses {alias}.{member} from '{spec}', but {target} does not export "
                                 f"{member!r} (it exports: {available})")
    return problems


def removed_exports(old: str, new: str) -> set[str]:
    before, _ = exports(old)
    after, incomplete = exports(new)
    return set() if incomplete else before - after
