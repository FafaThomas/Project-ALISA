"""Deterministic checks for the Dependency Auditor.

Reads dependency manifests from the files under review and, for context,
from the workspace. Nothing here touches the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MANIFESTS = ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "package.json")
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*(.*)$")


def _requirements(path: str, content: str) -> list[dict]:
    findings = []
    for number, raw in enumerate(content.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        match = _REQ_LINE.match(line)
        if match and not match.group(2).strip():
            findings.append({
                "severity": "low", "file": path,
                "issue": f"line {number}: {match.group(1)} has no version constraint",
                "suggestion": "pin a version or a compatible range for reproducible installs",
            })
    return findings


def _package_json(path: str, content: str) -> list[dict]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    findings = []
    for section in ("dependencies", "devDependencies"):
        for name, version in (data.get(section) or {}).items():
            if version in ("*", "latest", ""):
                findings.append({
                    "severity": "medium", "file": path,
                    "issue": f"{name} in {section} is not version-constrained ({version!r})",
                    "suggestion": "use a caret or exact version",
                })
    return findings


def review(files: list[tuple[str, str]]) -> list[dict]:
    findings: list[dict] = []
    for path, content in files:
        name = path.rsplit("/", 1)[-1].lower()
        if name.startswith("requirements") and name.endswith(".txt"):
            findings += _requirements(path, content)
        elif name == "package.json":
            findings += _package_json(path, content)
    return findings


def extra_context(query: str, workspace_root: Path) -> str | None:
    """Show the auditor the workspace's dependency manifests."""
    blocks = []
    for name in MANIFESTS:
        path = workspace_root / name
        if path.is_file() and path.stat().st_size < 20_000:
            blocks.append(f"### {name}\n```\n{path.read_text(encoding='utf-8', errors='replace')}\n```")
    return "\n\n".join(blocks) or None
