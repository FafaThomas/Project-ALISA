"""Load the coding agent's unit tree from its folders.

Every folder holding a ``unit.md`` is a unit. Units nest to any depth:

    coding_agent/unit.md                                   agent (the root)
    coding_agent/divisions/<division>/unit.md              division
    .../<division>/<specialist>/unit.md                    specialist
    .../<specialist>/<sub_specialist>/unit.md              sub-specialist (the specialist becomes a team)

A unit folder may also hold:

    tools.py           deterministic tools (see core/specialist.py)
    requirements.txt   Python packages this unit's tools need when served on its own

``unit.md`` starts with front matter between ``---`` lines. Values are JSON
when they look like JSON (lists, objects, numbers, booleans, quoted strings),
plain text otherwise. Keys:

    id, name            required; id must equal the folder name
    kind                required for specialists: analyst | planner | designer |
                        coordinator | builder | reviewer | repairer
    responsibility      required for specialists
    triggers            JSON list of words that make this unit relevant
    explicit_only       true: join a team only when the request names it
    lead                id of a unit directly under this one; required for the agent
                        (any unit in the tree) and divisions; optional for team specialists
    order               sort order among siblings
    default_members     ids used when a division is called and nothing matches
    attachments         agent only: {"specialist_id": "host_division_id"}
    model               model for this unit and every unit under it (see effective_model)
    think               true/false: let "thinking" models reason before answering (default false)
    temperature         sampling temperature, e.g. 0.2 (default CODING_AGENT_TEMPERATURE or 0.2)
    endpoint            URL of this unit's own HTTP service; parents then call it remotely
    faction             agent only: the faction it belongs to (prefix of its .env model variables)

The Markdown body is the unit's brief: a division's mission, a specialist's
instructions (added to its system prompt).

Every file is validated on load. A problem raises ``RegistryError`` naming the file.
"""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from .env import env_value, load_local_env

RoleKind = Literal["analyst", "planner", "designer", "coordinator", "builder", "reviewer", "repairer"]
ModelTier = Literal["planning", "coding", "review"]
Phase = Literal["analyze", "plan", "design", "build", "review"]
Level = Literal["agent", "division", "specialist"]

PHASES: tuple[Phase, ...] = ("analyze", "plan", "design", "build", "review")
PHASE_BY_KIND: dict[str, Phase] = {
    "analyst": "analyze", "planner": "plan", "designer": "design", "coordinator": "build",
    "builder": "build", "repairer": "build", "reviewer": "review",
}
TIER_BY_KIND: dict[str, ModelTier] = {
    "analyst": "planning", "planner": "planning", "designer": "planning", "coordinator": "planning",
    "builder": "coding", "repairer": "coding", "reviewer": "review",
}
UNIT_FILE = "unit.md"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ROOT_CHILDREN_DIR = "divisions"
_IGNORED_DIRS = {"__pycache__", "tests", "examples"}


class RegistryError(ValueError):
    """A unit folder is malformed."""


@dataclass(frozen=True)
class UnitSpec:
    path: str                    # "" for the agent, "development", "development/python_developer", ...
    id: str
    name: str
    level: Level
    depth: int
    folder: Path = field(compare=False)
    parent_path: str | None = None
    child_paths: tuple[str, ...] = ()
    kind: RoleKind | None = None
    responsibility: str = ""
    triggers: tuple[str, ...] = ()
    requires_explicit_request: bool = False
    instructions: str = ""
    lead: str | None = None
    order: int = 99
    default_members: tuple[str, ...] = ()
    attachments: tuple[tuple[str, str], ...] = ()
    model: str | None = None
    endpoint: str | None = None
    division: str = ""           # id of the division this unit sits in ("" for the agent)
    faction: str = ""            # agent only
    think: bool | None = None
    temperature: float | None = None

    @property
    def phase(self) -> Phase:
        if self.kind is None:
            raise AttributeError(f"{self.path or self.id} is a group, not a specialist")
        return PHASE_BY_KIND[self.kind]

    @property
    def tier(self) -> ModelTier:
        return TIER_BY_KIND[self.kind] if self.kind else "planning"

    @property
    def is_team(self) -> bool:
        """A specialist with sub-specialists."""
        return self.level == "specialist" and bool(self.child_paths)

    @property
    def members(self) -> tuple[str, ...]:
        """Ids of the units directly under this one, in order."""
        return tuple(p.rsplit("/", 1)[-1] for p in self.child_paths)

    @property
    def mission(self) -> str:
        return self.instructions

    @property
    def has_tools(self) -> bool:
        return (self.folder / "tools.py").is_file()

    @property
    def requirements_file(self) -> Path | None:
        path = self.folder / "requirements.txt"
        return path if path.is_file() else None

    def tools(self) -> ModuleType | None:
        return _load_tools(self.folder) if self.has_tools else None


# Older names, kept so call sites read naturally.
RoleSpec = UnitSpec
DivisionSpec = UnitSpec


@lru_cache(maxsize=None)
def _load_tools(folder: Path) -> ModuleType:
    path = folder / "tools.py"
    name = "coding_agent_tools_" + "_".join(folder.parts[-3:]).replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RegistryError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ parsing
def parse_front_matter(text: str, source: Path) -> tuple[dict[str, Any], str]:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise RegistryError(f"{source}: missing front matter (first line must be ---)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise RegistryError(f"{source}: front matter is not closed with ---") from None
    data: dict[str, Any] = {}
    for number, line in enumerate(lines[1:end], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise RegistryError(f"{source}:{number}: expected 'key: value'")
        key, raw = line.split(":", 1)
        raw = raw.strip()
        if raw[:1] in '[{"' or raw in ("true", "false") or re.fullmatch(r"-?\d+(\.\d+)?", raw):
            try:
                data[key.strip()] = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RegistryError(f"{source}:{number}: invalid value for {key.strip()!r}: {exc.msg}") from exc
        else:
            data[key.strip()] = raw
    return data, "\n".join(lines[end + 1:]).strip()


def _str(data: dict[str, Any], key: str, source: Path, *, required: bool = True) -> str | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{source}: '{key}' is required and must be text")
    return value.strip()


def _str_list(data: dict[str, Any], key: str, source: Path) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RegistryError(f"{source}: '{key}' must be a JSON list of strings")
    return tuple(value)


def _strip_title(body: str) -> str:
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _child_folders(folder: Path, *, strict: bool) -> list[Path]:
    """Sub-unit folders. Inside a division (strict) every subfolder must be a unit."""
    if not folder.is_dir():
        return []
    children = []
    for child in sorted(folder.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")) or child.name in _IGNORED_DIRS:
            continue
        if (child / UNIT_FILE).is_file():
            children.append(child)
        elif strict:
            raise RegistryError(f"{folder}: folder {child.name!r} has no {UNIT_FILE}")
    return children


# --------------------------------------------------------------------- tree
@dataclass(frozen=True)
class Tree:
    units: dict[str, UnitSpec]   # by path

    @classmethod
    def load(cls, package_root: Path = PACKAGE_ROOT) -> "Tree":
        root_file = package_root / UNIT_FILE
        if not root_file.is_file():
            raise RegistryError(f"missing agent {UNIT_FILE}: {root_file}")
        units: dict[str, UnitSpec] = {}
        seen_ids: dict[str, Path] = {}

        def load_unit(folder: Path, path: str, depth: int, parent: str | None, division: str) -> None:
            source = folder / UNIT_FILE
            data, body = parse_front_matter(source.read_text(encoding="utf-8"), source)
            unit_id = _str(data, "id", source)
            if depth > 0 and unit_id != folder.name:
                raise RegistryError(f"{source}: id {unit_id!r} must match its folder name {folder.name!r}")
            if unit_id in seen_ids:
                raise RegistryError(f"{source}: id {unit_id!r} is already used by {seen_ids[unit_id]}")
            seen_ids[unit_id] = source
            level: Level = "agent" if depth == 0 else "division" if depth == 1 else "specialist"
            division = unit_id if depth == 1 else division

            child_dir = folder / ROOT_CHILDREN_DIR if depth == 0 else folder
            child_paths = []
            for child in _child_folders(child_dir, strict=depth == 1):
                child_path = f"{path}/{child.name}" if path else child.name
                load_unit(child, child_path, depth + 1, path, division)
                child_paths.append(child_path)
            child_paths.sort(key=lambda p: (units[p].order, p))
            child_ids = [p.rsplit("/", 1)[-1] for p in child_paths]

            kind = data.get("kind")
            if level == "specialist":
                if kind not in PHASE_BY_KIND:
                    raise RegistryError(f"{source}: 'kind' must be one of {sorted(PHASE_BY_KIND)}")
                responsibility = _str(data, "responsibility", source)
            else:
                if kind is not None:
                    raise RegistryError(f"{source}: only specialists have a 'kind'")
                responsibility = str(data.get("responsibility") or "")

            lead = _str(data, "lead", source, required=level != "specialist")
            if lead is not None and level != "agent" and lead not in child_ids:
                raise RegistryError(f"{source}: lead {lead!r} is not a unit directly under this one")
            defaults = _str_list(data, "default_members", source)
            if any(d not in child_ids for d in defaults):
                raise RegistryError(f"{source}: default_members must list units directly under this one")
            attachments = data.get("attachments", {})
            if not isinstance(attachments, dict) or (attachments and depth != 0):
                raise RegistryError(f"{source}: 'attachments' is an agent-level JSON object")
            order = data.get("order", 99)
            if isinstance(order, bool) or not isinstance(order, int):
                raise RegistryError(f"{source}: 'order' must be an integer")
            explicit = data.get("explicit_only", False)
            if not isinstance(explicit, bool):
                raise RegistryError(f"{source}: 'explicit_only' must be true or false")
            think = data.get("think")
            if think is not None and not isinstance(think, bool):
                raise RegistryError(f"{source}: 'think' must be true or false")
            temperature = data.get("temperature")
            if temperature is not None and (isinstance(temperature, bool)
                                            or not isinstance(temperature, (int, float)) or temperature < 0):
                raise RegistryError(f"{source}: 'temperature' must be a number >= 0")
            endpoint = _str(data, "endpoint", source, required=False)
            if endpoint and not endpoint.startswith(("http://", "https://")):
                raise RegistryError(f"{source}: 'endpoint' must be an http(s) URL")

            units[path] = UnitSpec(
                path=path, id=unit_id, name=_str(data, "name", source), level=level, depth=depth,
                folder=folder, parent_path=parent, child_paths=tuple(child_paths), kind=kind,
                responsibility=responsibility,
                triggers=tuple(t.lower() for t in _str_list(data, "triggers", source)),
                requires_explicit_request=explicit, instructions=_strip_title(body), lead=lead, order=order,
                default_members=defaults, attachments=tuple(attachments.items()),
                model=_str(data, "model", source, required=False), endpoint=endpoint, division=division,
                faction=(_str(data, "faction", source, required=False) or "") if depth == 0 else "",
                think=think, temperature=float(temperature) if temperature is not None else None,
            )

        load_unit(package_root, "", 0, None, "")
        tree = cls(units)
        root, by_id = tree.root, tree.by_id
        if root.lead not in by_id:
            raise RegistryError(f"{root_file}: lead {root.lead!r} is not a unit in the tree")
        division_ids = {d.id for d in tree.divisions}
        for role_id, host in root.attachments:
            if role_id not in by_id or host not in division_ids:
                raise RegistryError(f"{root_file}: attachment {role_id!r} -> {host!r} names an unknown unit")
        return tree

    # ---------------------------------------------------------------- lookups
    @property
    def root(self) -> UnitSpec:
        return self.units[""]

    @property
    def by_id(self) -> dict[str, UnitSpec]:
        return {u.id: u for u in self.units.values()}

    def get(self, ref: str) -> UnitSpec:
        """A unit by path ("development/python_developer") or id ("python_developer")."""
        ref = (ref or "").strip().strip("/")
        if ref in self.units:
            return self.units[ref]
        unit = self.by_id.get(ref)
        if unit is None:
            raise KeyError(f"unknown unit {ref!r}")
        return unit

    def children(self, unit: UnitSpec) -> list[UnitSpec]:
        return [self.units[p] for p in unit.child_paths]

    def parent(self, unit: UnitSpec) -> UnitSpec | None:
        return None if unit.parent_path is None else self.units[unit.parent_path]

    def ancestors(self, unit: UnitSpec) -> list[UnitSpec]:
        out, current = [], self.parent(unit)
        while current is not None:
            out.append(current)
            current = self.parent(current)
        return out

    def subtree(self, unit: UnitSpec) -> list[UnitSpec]:
        out = [unit]
        for child in self.children(unit):
            out.extend(self.subtree(child))
        return out

    @property
    def divisions(self) -> list[UnitSpec]:
        return self.children(self.root)

    def division_of(self, unit: UnitSpec) -> UnitSpec | None:
        return self.units.get(unit.division) if unit.division else None

    @property
    def roster(self) -> list[UnitSpec]:
        """Specialists directly in a division: the units the Director can put on a team."""
        return [s for d in self.divisions for s in self.children(d)]

    @property
    def specialists(self) -> list[UnitSpec]:
        return [u for u in self.units.values() if u.level == "specialist"]

    def unit_env_var(self, unit: UnitSpec, setting: str) -> str:
        """A unit's .env variable, named after its place in the hierarchy:
        FORGE_CODING_AGENT_<SETTING>, FORGE_CODING_AGENT_DEVELOPMENT_<SETTING>, ..."""
        root = self.root
        parts = [root.faction, root.id, *unit.path.split("/")] if unit.path else [root.faction, root.id]
        name = "_".join(p for p in parts if p)
        return re.sub(r"[^A-Za-z0-9]+", "_", name).upper() + "_" + setting.upper()

    def model_env_var(self, unit: UnitSpec) -> str:
        return self.unit_env_var(unit, "MODEL")

    def _lookup(self, unit: UnitSpec, setting: str, attr: str):
        """First value found walking up from ``unit``: .env variable, then unit.md field."""
        load_local_env()
        for candidate in [unit, *self.ancestors(unit)]:
            var = self.unit_env_var(candidate, setting)
            value = env_value(var)
            if value is not None:
                return value, f".env {var}"
            declared = getattr(candidate, attr)
            if declared is not None:
                return declared, f"unit.md ({candidate.path or candidate.id})"
        return None, None

    def effective_think(self, unit: UnitSpec) -> tuple[bool, str]:
        """Whether a thinking model may reason before answering. Off unless turned on."""
        value, source = self._lookup(unit, "THINK", "think")
        if value is None:
            return False, "default (off)"
        if isinstance(value, str):
            if value.strip().lower() not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                raise RegistryError(f"{source}: THINK must be true or false, got {value!r}")
            value = value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value), source  # type: ignore[return-value]

    def effective_temperature(self, unit: UnitSpec) -> tuple[float, str]:
        value, source = self._lookup(unit, "TEMPERATURE", "temperature")
        if value is None:
            fallback = env_value("CODING_AGENT_TEMPERATURE")
            value, source = (fallback, ".env CODING_AGENT_TEMPERATURE") if fallback else (0.2, "default")
        try:
            return float(value), source  # type: ignore[arg-type]
        except ValueError as exc:
            raise RegistryError(f"{source}: temperature must be a number, got {value!r}") from exc

    def effective_model(self, unit: UnitSpec) -> tuple[str | None, str | None]:
        """(model, where it came from), or (None, None) to use the .env tier default.

        Looks from the unit upward: the unit, its team, its division, the agent.
        At each level the unit's .env variable is checked first, then ``model:``
        in its unit.md; the first one set wins.
        """
        load_local_env()
        for candidate in [unit, *self.ancestors(unit)]:
            var = self.model_env_var(candidate)
            value = env_value(var)
            if value:
                return value, f".env {var}"
            if candidate.model:
                return candidate.model, f"unit.md ({candidate.path or candidate.id})"
        return None, None

    def team_of(self, unit: UnitSpec) -> UnitSpec | None:
        """The team specialist a sub-specialist belongs to."""
        parent = self.parent(unit)
        return parent if parent is not None and parent.level == "specialist" else None

    def describe(self, unit: UnitSpec | None = None) -> dict[str, Any]:
        unit = unit or self.root
        model, model_source = self.effective_model(unit)
        data: dict[str, Any] = {
            "id": unit.id, "path": unit.path, "name": unit.name, "level": unit.level,
            "lead": unit.lead, "model": unit.model, "model_env_var": self.model_env_var(unit),
            "effective_model": model or f".env {unit.tier} tier",
            "model_source": model_source or f".env {unit.tier} tier default", "endpoint": unit.endpoint,
            "has_tools": unit.has_tools, "has_requirements": unit.requirements_file is not None,
        }
        if unit.level == "specialist":
            data.update(kind=unit.kind, phase=unit.phase, tier=unit.tier, responsibility=unit.responsibility,
                        is_team=unit.is_team)
        else:
            data["mission"] = unit.mission
        if unit.level == "agent":
            data["attachments"] = dict(unit.attachments)
        data["units"] = [self.describe(child) for child in self.children(unit)]
        return data


@lru_cache(maxsize=1)
def default_tree() -> Tree:
    return Tree.load()


def get_role(role_id: str) -> UnitSpec:
    return default_tree().get(role_id)


def get_division(division_id: str) -> UnitSpec:
    unit = default_tree().get(division_id)
    if unit.level != "division":
        raise KeyError(f"{division_id!r} is not a division")
    return unit
