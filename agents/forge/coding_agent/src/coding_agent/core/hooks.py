"""Per-unit behaviour overrides.

A unit's ``agent.py`` defines a subclass of ``UnitHooks`` (through
``coding_agent.unit_entry.UnitAgent``). The specialist runner calls these
hooks around its own steps, so one specialist can behave differently without
copying the shared runtime.

The base implementations do nothing; override only what the unit needs.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import RoleOutput

LOGGER = logging.getLogger(__name__)
ACTIONS = ("consult", "coordinate", "build", "repair", "review")


class UnitHooks:
    """Override in a unit's agent.py to change how that unit works."""

    def extra_context(self, query: str, workspace_root: Path) -> str | None:
        """Text added to this unit's context before its model is called."""
        return None

    def review(self, files: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Deterministic findings over (path, content) pairs; each has severity, issue, file?, suggestion?."""
        return []

    def after_output(self, output: RoleOutput, action: str) -> RoleOutput:
        """Inspect or adjust this unit's output. ``action`` is one of ACTIONS."""
        return output


@lru_cache(maxsize=None)
def load_hooks(folder: Path) -> UnitHooks | None:
    """The ``UnitHooks`` subclass defined in ``folder/agent.py``, instantiated; None if there is none."""
    path = folder / "agent.py"
    if not path.is_file():
        return None
    name = "coding_agent_unit_" + "_".join(folder.parts[-3:]).replace("-", "_").replace(".", "_")
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        classes = [obj for obj in vars(module).values()
                   if inspect.isclass(obj) and issubclass(obj, UnitHooks) and obj.__module__ == module.__name__]
        return classes[0]() if classes else None
    except Exception as exc:  # a broken agent.py must not break the run
        LOGGER.warning("could not load hooks from %s: %s", path, exc)
        return None
