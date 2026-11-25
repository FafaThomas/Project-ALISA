"""What each unit folder's agent.py, api.py and cli.py are built on.

Every unit folder has three small entry files that bind the shared runtime to
that unit:

    agent.py   the unit as a Python object (UnitAgent subclass) + optional hooks
    api.py     python api.py --allow-root D:/work --port 8801   (serve this unit)
    cli.py     python cli.py D:/work/app "<task>"              (run this unit once)

They locate their unit from their own file path, so they cannot point at the
wrong unit. ``coding-agent scaffold`` creates them for new units.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .core.hooks import UnitHooks
from .core.registry import PACKAGE_ROOT, ROOT_CHILDREN_DIR, UNIT_FILE, Tree, default_tree


def locate_unit(file: str | Path) -> tuple[Path, str]:
    """(package root of the tree, unit path) for an entry file inside a unit folder."""
    folder = Path(file).resolve().parent
    if not (folder / UNIT_FILE).is_file():
        raise ValueError(f"{folder} is not a unit folder (no {UNIT_FILE})")
    for candidate in [folder, *folder.parents]:
        if (candidate / UNIT_FILE).is_file() and (candidate / ROOT_CHILDREN_DIR).is_dir():
            if candidate == folder:
                return candidate, ""
            return candidate, folder.relative_to(candidate / ROOT_CHILDREN_DIR).as_posix()
    raise ValueError(f"{folder} is not inside a coding agent unit tree")


def _tree_for(package_root: Path) -> Tree:
    return default_tree() if package_root == PACKAGE_ROOT else Tree.load(package_root)


class UnitAgent(UnitHooks):
    """A unit as a Python object. Subclass it in the unit's agent.py.

    ``kwargs`` go to ``CodingAgent`` (router, use_director_llm, max_repairs, ...).
    Nothing is loaded until the unit is first used.
    """

    unit_file: str = ""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._unit = None

    @property
    def unit_path(self) -> str:
        return locate_unit(self.unit_file)[1]

    @property
    def unit(self):
        if self._unit is None:
            from .agent import CodingAgent

            package_root, path = locate_unit(self.unit_file)
            kwargs = dict(self._kwargs)
            kwargs.setdefault("tree", _tree_for(package_root))
            self._unit = CodingAgent(**kwargs).unit(path)
        return self._unit

    def run(self, request, invocation, *, emit=None):
        return self.unit.run(request, invocation, emit=emit)

    def describe(self) -> dict[str, Any]:
        return self.unit.describe()


def load_unit_agent(ref: str | None = None, **kwargs: Any) -> UnitAgent:
    """The UnitAgent class from a unit's agent.py, instantiated (a plain UnitAgent if it has none)."""
    from .core.hooks import load_hooks

    tree = kwargs.get("tree") or default_tree()
    spec = tree.get(ref or "")
    hooks = load_hooks(spec.folder) if spec.level != "agent" else None
    cls = type(hooks) if isinstance(hooks, UnitAgent) else UnitAgent
    agent = cls(**kwargs)
    if not agent.unit_file:
        agent.unit_file = str(spec.folder / "agent.py")
    return agent


def run_unit_cli(file: str | Path, argv: list[str] | None = None) -> int:
    """``coding-agent run`` scoped to the unit that owns ``file``."""
    from .cli import main

    package_root, path = locate_unit(file)
    if package_root != PACKAGE_ROOT:
        raise SystemExit("entry files can only run units of the installed coding agent")
    args = list(sys.argv[1:] if argv is None else argv)
    return main(["run", *args, *(["--unit", path] if path else [])])


def serve_unit(file: str | Path, argv: list[str] | None = None) -> int:
    """``coding-agent serve`` scoped to the unit that owns ``file``."""
    from .cli import main

    package_root, path = locate_unit(file)
    if package_root != PACKAGE_ROOT:
        raise SystemExit("entry files can only serve units of the installed coding agent")
    args = list(sys.argv[1:] if argv is None else argv)
    return main(["serve", *args, *(["--unit", path] if path else [])])
