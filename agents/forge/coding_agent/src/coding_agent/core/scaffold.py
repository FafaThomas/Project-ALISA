"""Create and check each unit folder's entry files (agent.py, api.py, cli.py).

The agent's own folder is the ``coding_agent`` package, whose agent.py, api.py
and cli.py are the shared implementation; it is checked, never generated.
"""

from __future__ import annotations

import re
from pathlib import Path

from .registry import Tree, UnitSpec

ENTRY_FILES = ("agent.py", "api.py", "cli.py")
MARKER = "coding_agent.unit_entry"

_HOOKS = '''
    # Optional: override any of these to change how this unit works.
    #
    # def extra_context(self, query, workspace_root):
    #     """Text added to this unit's context before its model is called."""
    #     return None
    #
    # def review(self, files):
    #     """Deterministic findings over (path, content) pairs:
    #     [{"severity": "medium", "file": path, "issue": "...", "suggestion": "..."}]"""
    #     return []
    #
    # def after_output(self, output, action):
    #     """Inspect or adjust this unit's output.
    #     action: consult | coordinate | build | repair | review"""
    #     return output
'''

AGENT_TEMPLATE = '''"""{name} ({level}{where}): this unit as a Python object.

    from coding_agent.unit_entry import load_unit_agent
    unit = load_unit_agent("{path}")
    report = unit.run(request, invocation)

Its role, triggers and model are in unit.md. Shared behaviour lives in
coding_agent/core; anything specific to this unit goes in the class below.
"""

from coding_agent.unit_entry import UnitAgent


class {cls}(UnitAgent):
    unit_file = __file__
{hooks}'''

API_TEMPLATE = '''"""Serve the {name} ({level}) as its own HTTP service.

    python api.py --allow-root D:/work --port 8801

Same options as `coding-agent serve`, already scoped to this unit. To have
parent units call this service, set `endpoint: http://<host>:<port>` in unit.md.
"""

from coding_agent.unit_entry import serve_unit

if __name__ == "__main__":
    raise SystemExit(serve_unit(__file__))
'''

CLI_TEMPLATE = '''"""Run the {name} ({level}) on one task from the command line.

    python cli.py D:/work/app "<task>" [--propose-only] [--json]

Same options as `coding-agent run`, already scoped to this unit.
"""

from coding_agent.unit_entry import run_unit_cli

if __name__ == "__main__":
    raise SystemExit(run_unit_cli(__file__))
'''


def class_name(unit: UnitSpec) -> str:
    words = re.findall(r"[A-Za-z0-9]+", unit.name) or [unit.id]
    name = "".join(w[:1].upper() + w[1:].lower() for w in words)
    return name if name[:1].isalpha() else f"Unit{name}"


def render(unit: UnitSpec, tree: Tree) -> dict[str, str]:
    division = tree.division_of(unit)
    team = tree.team_of(unit)
    where = ""
    if unit.level == "specialist" and division is not None:
        where = f", {division.name} division" + (f", {team.name} team" if team else "")
    values = {"name": unit.name, "level": unit.level, "path": unit.path, "where": where, "cls": class_name(unit),
              "hooks": _HOOKS if unit.level == "specialist" else ""}
    return {
        "agent.py": AGENT_TEMPLATE.format(**values),
        "api.py": API_TEMPLATE.format(**values),
        "cli.py": CLI_TEMPLATE.format(**values),
    }


def scaffold(tree: Tree, units: list[UnitSpec] | None = None, *, force: bool = False) -> list[Path]:
    """Write missing entry files (all of them with ``force``). Returns the files written."""
    written = []
    for unit in units if units is not None else list(tree.units.values()):
        if unit.level == "agent":
            continue
        for filename, text in render(unit, tree).items():
            target = unit.folder / filename
            if target.exists() and not force:
                continue
            target.write_text(text, encoding="utf-8", newline="\n")
            written.append(target)
    return written


def check(tree: Tree) -> list[str]:
    """Problems with entry files: missing, or not built on coding_agent.unit_entry."""
    problems = []
    for unit in tree.units.values():
        for filename in ENTRY_FILES:
            target = unit.folder / filename
            label = f"{unit.path or unit.id}/{filename}"
            if not target.is_file():
                problems.append(f"{label}: missing (run `coding-agent scaffold`)")
            elif unit.level != "agent" and MARKER not in target.read_text(encoding="utf-8"):
                problems.append(f"{label}: does not use {MARKER}")
    return problems
