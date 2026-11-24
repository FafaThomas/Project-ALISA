"""Load the agent's hierarchy from its folders.

    agent.md                                                agent brief, lead, attachments
    divisions/<division_id>/division.md                     division brief + lead
    divisions/<division_id>/<specialist_id>/specialist.md   specialist definition
    divisions/<division_id>/<specialist_id>/tools.py        optional deterministic tools

Each Markdown file starts with front matter between ``---`` lines. Values are
JSON when they look like JSON (lists, numbers, booleans, quoted strings) and
plain text otherwise. The Markdown body of ``specialist.md`` becomes part of the
specialist's system prompt.

Adding a specialist means adding a folder; nothing else needs editing. Every
file is validated at load time, and a bad file raises ``RegistryError``
naming the file.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

RoleKind = Literal["analyst", "planner", "designer", "coordinator", "builder", "reviewer", "repairer"]
ModelTier = Literal["planning", "coding", "review"]
Phase = Literal["analyze", "plan", "design", "build", "review"]

PHASES: tuple[Phase, ...] = ("analyze", "plan", "design", "build", "review")
PHASE_BY_KIND: dict[str, Phase] = {
    "analyst": "analyze", "planner": "plan", "designer": "design", "coordinator": "build",
    "builder": "build", "repairer": "build", "reviewer": "review",
}
TIER_BY_KIND: dict[str, ModelTier] = {
    "analyst": "planning", "planner": "planning", "designer": "planning", "coordinator": "planning",
    "builder": "coding", "repairer": "coding", "reviewer": "review",
}
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = PACKAGE_ROOT / "divisions"


class RegistryError(ValueError):
    """A division or specialist folder is malformed."""


@dataclass(frozen=True)
class RoleSpec:
    id: str
    name: str
    division: str
    responsibility: str
    kind: RoleKind
    triggers: tuple[str, ...] = ()
    requires_explicit_request: bool = False
    instructions: str = ""
    folder: Path | None = field(default=None, compare=False)
    phase: Phase = field(init=False)
    tier: ModelTier = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase", PHASE_BY_KIND[self.kind])
        object.__setattr__(self, "tier", TIER_BY_KIND[self.kind])

    @property
    def has_tools(self) -> bool:
        return self.folder is not None and (self.folder / "tools.py").is_file()

    def tools(self) -> ModuleType | None:
        return _load_tools(self.folder) if self.has_tools else None


@dataclass(frozen=True)
class DivisionSpec:
    id: str
    name: str
    lead: str
    order: int
    mission: str
    default_members: tuple[str, ...] = ()
    members: tuple[str, ...] = ()
    folder: Path | None = field(default=None, compare=False)


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    lead: str
    mission: str
    attachments: tuple[tuple[str, str], ...] = ()  # (specialist id, host division id)


@lru_cache(maxsize=None)
def _load_tools(folder: Path) -> ModuleType:
    path = folder / "tools.py"
    spec = importlib.util.spec_from_file_location(f"coding_agent_tools_{folder.parent.name}_{folder.name}", path)
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
        if raw[:1] in '[{"' or raw in ("true", "false") or raw.lstrip("-").isdigit():
            try:
                data[key.strip()] = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RegistryError(f"{source}:{number}: invalid value for {key.strip()!r}: {exc.msg}") from exc
        else:
            data[key.strip()] = raw
    return data, "\n".join(lines[end + 1:]).strip()


def _require(data: dict[str, Any], key: str, source: Path, kind: type = str) -> Any:
    value = data.get(key)
    if not isinstance(value, kind) or (isinstance(value, str) and not value.strip()):
        raise RegistryError(f"{source}: '{key}' is required and must be a {kind.__name__}")
    return value


def _strip_title(body: str) -> str:
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def load_role(path: Path, division_id: str) -> RoleSpec:
    data, body = parse_front_matter(path.read_text(encoding="utf-8"), path)
    role_id = _require(data, "id", path)
    if role_id != path.parent.name:
        raise RegistryError(f"{path}: id {role_id!r} must match its folder name {path.parent.name!r}")
    kind = _require(data, "kind", path)
    if kind not in PHASE_BY_KIND:
        raise RegistryError(f"{path}: unknown kind {kind!r}; expected one of {sorted(PHASE_BY_KIND)}")
    triggers = data.get("triggers", [])
    if not isinstance(triggers, list) or not all(isinstance(t, str) for t in triggers):
        raise RegistryError(f"{path}: 'triggers' must be a JSON list of strings")
    explicit = data.get("explicit_only", False)
    if not isinstance(explicit, bool):
        raise RegistryError(f"{path}: 'explicit_only' must be true or false")
    return RoleSpec(
        id=role_id, name=_require(data, "name", path), division=division_id,
        responsibility=_require(data, "responsibility", path), kind=kind,
        triggers=tuple(t.lower() for t in triggers), requires_explicit_request=explicit,
        instructions=_strip_title(body), folder=path.parent,
    )


def load_division(folder: Path) -> tuple[DivisionSpec, list[RoleSpec]]:
    path = folder / "division.md"
    if not path.is_file():
        raise RegistryError(f"{folder}: missing division.md")
    data, body = parse_front_matter(path.read_text(encoding="utf-8"), path)
    division_id = _require(data, "id", path)
    if division_id != folder.name:
        raise RegistryError(f"{path}: id {division_id!r} must match its folder name {folder.name!r}")
    roles = [load_role(child / "specialist.md", division_id)
             for child in sorted(folder.iterdir()) if child.is_dir() and (child / "specialist.md").is_file()]
    stray = [c.name for c in folder.iterdir()
             if c.is_dir() and not (c / "specialist.md").is_file() and not c.name.startswith(("_", "."))]
    if stray:
        raise RegistryError(f"{folder}: specialist folder(s) without specialist.md: {sorted(stray)}")
    member_ids = tuple(r.id for r in roles)
    lead = _require(data, "lead", path)
    if lead not in member_ids:
        raise RegistryError(f"{path}: lead {lead!r} is not a specialist in this division")
    defaults = data.get("default_members", [])
    if not isinstance(defaults, list) or any(d not in member_ids for d in defaults):
        raise RegistryError(f"{path}: default_members must list specialists in this division")
    order = data.get("order", 99)
    if isinstance(order, bool) or not isinstance(order, int):
        raise RegistryError(f"{path}: 'order' must be an integer")
    spec = DivisionSpec(
        id=division_id, name=_require(data, "name", path), lead=lead, order=order,
        mission=_strip_title(body), default_members=tuple(defaults), members=member_ids, folder=folder,
    )
    return spec, roles


def load_agent(path: Path, roles: dict[str, RoleSpec], division_ids: set[str]) -> AgentSpec:
    data, body = parse_front_matter(path.read_text(encoding="utf-8"), path)
    lead = _require(data, "lead", path)
    if lead not in roles:
        raise RegistryError(f"{path}: lead {lead!r} is not a known specialist")
    attachments = data.get("attachments", {})
    if not isinstance(attachments, dict):
        raise RegistryError(f"{path}: 'attachments' must be a JSON object of specialist -> division")
    for role_id, host in attachments.items():
        if role_id not in roles or host not in division_ids:
            raise RegistryError(f"{path}: attachment {role_id!r} -> {host!r} names an unknown specialist or division")
    return AgentSpec(
        id=_require(data, "id", path), name=_require(data, "name", path), lead=lead,
        mission=_strip_title(body), attachments=tuple(attachments.items()),
    )


@dataclass(frozen=True)
class Registry:
    agent: AgentSpec
    divisions: tuple[DivisionSpec, ...]
    roles: tuple[RoleSpec, ...]

    @classmethod
    def load(cls, root: Path = DEFAULT_ROOT, agent_file: Path | None = None) -> "Registry":
        if not root.is_dir():
            raise RegistryError(f"divisions folder not found: {root}")
        divisions, roles, seen = [], [], {}
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
            spec, members = load_division(folder)
            for role in members:
                if role.id in seen:
                    raise RegistryError(f"specialist id {role.id!r} appears in {seen[role.id]} and {spec.id}")
                seen[role.id] = spec.id
            divisions.append(spec)
            roles.extend(members)
        divisions.sort(key=lambda d: (d.order, d.id))
        order = {d.id: d.order for d in divisions}
        roles.sort(key=lambda r: (order[r.division], r.id))
        agent_path = agent_file or root.parent / "agent.md"
        if not agent_path.is_file():
            raise RegistryError(f"missing agent brief: {agent_path}")
        agent = load_agent(agent_path, {r.id: r for r in roles}, set(order))
        return cls(agent, tuple(divisions), tuple(roles))

    def role(self, role_id: str) -> RoleSpec:
        for role in self.roles:
            if role.id == role_id:
                return role
        raise KeyError(f"unknown specialist {role_id!r}")

    def division(self, division_id: str) -> DivisionSpec:
        for division in self.divisions:
            if division.id == division_id:
                return division
        raise KeyError(f"unknown division {division_id!r}")


REGISTRY = Registry.load()
ROSTER: tuple[RoleSpec, ...] = REGISTRY.roles
ROLES: dict[str, RoleSpec] = {r.id: r for r in ROSTER}
DIVISIONS: dict[str, DivisionSpec] = {d.id: d for d in REGISTRY.divisions}
AGENT: AgentSpec = REGISTRY.agent


def get_role(role_id: str) -> RoleSpec:
    return REGISTRY.role(role_id)


def get_division(division_id: str) -> DivisionSpec:
    return REGISTRY.division(division_id)


def roles_by_division() -> dict[str, list[RoleSpec]]:
    return {d.id: [ROLES[m] for m in d.members] for d in REGISTRY.divisions}
