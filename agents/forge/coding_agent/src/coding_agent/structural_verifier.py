"""Conservative structural checks for JavaScript/TypeScript React components.

This verifier intentionally reports only high-confidence prop mismatches. It is
not a type checker and skips call sites using spread attributes or components
whose props cannot be inferred safely.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
IGNORED_DIRS = {".git", "node_modules", "dist", "build", ".next", ".venv", "venv", ".coding-agent"}


@dataclass(frozen=True)
class StructuralIssue:
    code: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.code}] {self.message}"


def _source_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        files.append(path)
        if len(files) >= 2000:
            break
    return files


def _component_props(source: str) -> set[str] | None:
    """Infer destructured props from common function component declarations."""
    patterns = (
        r"(?:function\s+[A-Z][\w$]*\s*|(?:const|let|var)\s+[A-Z][\w$]*\s*=\s*(?:\([^)]*\)\s*=>|[\w$]+\s*=>))\s*\(\s*\{(?P<props>[^{}]*)\}",
        r"(?:const|let|var)\s+[A-Z][\w$]*\s*=\s*\(\s*\{(?P<props>[^{}]*)\}\s*\)\s*=>",
        r"function\s+[A-Z][\w$]*\s*\(\s*\{(?P<props>[^{}]*)\}\s*\)",
    )
    for pattern in patterns:
        match = re.search(pattern, source, re.MULTILINE)
        if match:
            raw = match.group("props")
            if "..." in raw:
                return None
            props = set()
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                name = re.match(r"([A-Za-z_$][\w$]*)", part)
                if name:
                    props.add(name.group(1))
            return props
    return None


def _jsx_calls(source: str):
    # A conservative single-line/multiline tag scanner. JSX attribute values
    # may contain nested braces; we only need top-level attribute names.
    tag_re = re.compile(r"<([A-Z][A-Za-z0-9_$]*)\b(?P<body>[^<>]*?)(?:/?>)", re.DOTALL)
    for match in tag_re.finditer(source):
        body = match.group("body")
        if "..." in body:
            continue
        attrs = set(re.findall(r"(?<![\w$.-])([A-Za-z_$][\w$:-]*)\s*(?:=|(?=\s|$))", body))
        yield match.group(1), attrs, source.count("\n", 0, match.start()) + 1


def analyze_workspace(workspace: str | Path) -> tuple[StructuralIssue, ...]:
    root = Path(workspace).resolve()
    files = _source_files(root)
    sources: dict[Path, str] = {}
    for path in files:
        try:
            sources[path] = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue

    definitions: dict[str, tuple[Path, set[str]]] = {}
    for path, source in sources.items():
        props = _component_props(source)
        if props is None:
            continue
        names = re.findall(r"(?:function\s+([A-Z][\w$]*)|(?:const|let|var)\s+([A-Z][\w$]*)\s*=)", source)
        for pair in names:
            name = next((item for item in pair if item), None)
            if name:
                definitions[name] = (path, props)

    issues: list[StructuralIssue] = []
    for caller_path, source in sources.items():
        for name, attrs, line in _jsx_calls(source):
            definition = definitions.get(name)
            if definition is None:
                continue
            component_path, expected = definition
            if caller_path == component_path:
                continue
            missing = sorted(expected - attrs)
            if missing:
                issues.append(StructuralIssue(
                    code="react-missing-component-prop",
                    path=caller_path.relative_to(root).as_posix(),
                    line=line,
                    message=(f"<{'%s' % name}> does not provide required destructured prop(s): "
                             + ", ".join(missing)
                             + f" (component: {component_path.relative_to(root).as_posix()})"),
                ))

    return tuple(issues)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python -m coding_agent.structural_verifier WORKSPACE", file=sys.stderr)
        return 2
    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"Workspace does not exist: {root}", file=sys.stderr)
        return 2
    issues = analyze_workspace(root)
    if issues:
        print("\n".join(issue.render() for issue in issues), file=sys.stderr)
        print(f"Structural verification failed: {len(issues)} issue(s).", file=sys.stderr)
        return 1
    print("Structural verification passed: no high-confidence React component prop mismatches found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
