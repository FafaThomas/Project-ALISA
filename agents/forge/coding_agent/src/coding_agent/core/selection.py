"""Team selection: pick a proportionate team for one request.

At the agent level the Engineering Director (the agent's lead) owns this step:
a deterministic pass scores every specialist's triggers against the request
and the workspace, then the Director model may refine the shortlist. Whatever
the model says is validated against the tree and the size caps, and the
deterministic team is the fallback.

When a division is called directly, ``for_division`` picks members from that
division only, without a model call. Team specialists (specialists with
sub-specialists) are picked as one unit; they route to their own members.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .llm import LLMError, ModelRouter
from .models import CodingRequest
from .prompts import DIRECTOR_SYSTEM, TEAM_SCHEMA
from .registry import DivisionSpec, RoleSpec, Tree, default_tree

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_TEAM = 10
# "builder" counts core developers (Development division); "support" counts
# builders from QA, DevOps and Documentation (tests, pipelines, docs).
CAPS = {"analyst": 1, "planner": 2, "designer": 2, "builder": 3, "support": 2, "reviewer": 2}
# When trimming to the size cap, drop the least essential kinds first.
TRIM_ORDER = ("reviewer", "planner", "support", "designer", "analyst", "builder")
PHASE_ORDER = {"analyze": 0, "plan": 1, "design": 2, "build": 3, "review": 4}
STANDBY = ("debugging_specialist",)

LANGUAGE_BUILDERS = {
    ".py": "python_developer",
    ".cs": "dotnet_developer", ".csproj": "dotnet_developer", ".sln": "dotnet_developer",
    ".tsx": "frontend_developer", ".jsx": "frontend_developer", ".vue": "frontend_developer",
    ".svelte": "frontend_developer", ".css": "frontend_developer", ".html": "frontend_developer",
    ".ts": "full_stack_developer", ".js": "full_stack_developer",
    ".kt": "mobile_developer", ".swift": "mobile_developer", ".dart": "mobile_developer",
    ".sql": "database_developer",
    ".go": "backend_developer", ".java": "backend_developer", ".rs": "backend_developer",
}
FALLBACK_BUILDER = "full_stack_developer"
SUBSUMED_BY = {
    "backend_developer": ("python_developer", "dotnet_developer"),
    "full_stack_developer": ("frontend_developer",),
}

# Triggers that are word stems ("integrat" -> integrate, integration) match as
# prefixes; every other alphanumeric trigger must match a whole word (plurals ok).
_STEMS = frozenset({"integrat", "scalab", "optimi", "vulnerab", "dependenc", "deprecat"})


def trigger_hits(trigger: str, text: str) -> bool:
    if trigger != trigger.strip() or not re.fullmatch(r"[a-z0-9 ]+", trigger):
        return trigger in text  # punctuation-bearing triggers: ".py", "c#", "go "
    if trigger in _STEMS:
        return re.search(rf"\b{re.escape(trigger)}", text) is not None
    return re.search(rf"\b{re.escape(trigger)}(?:s|es)?\b", text) is not None


def score_units(text: str, units: list[RoleSpec]) -> dict[str, float]:
    lowered = f" {text.lower()} "
    scores: dict[str, float] = {}
    for unit in units:
        hits = [t for t in unit.triggers if trigger_hits(t, lowered)]
        if hits:
            scores[unit.id] = float(len(hits))
    # A language-specific developer covers the generic one for the same layer.
    for generic, specific in SUBSUMED_BY.items():
        if generic in scores and any(s in scores for s in specific):
            del scores[generic]
    return scores


def score_roles(text: str, tree: Tree | None = None) -> dict[str, float]:
    return score_units(text, (tree or default_tree()).roster)


def division_needs_lead(division: DivisionSpec, builders: list[RoleSpec]) -> bool:
    """A division lead splits the work when there is something to split."""
    return bool(builders) and (division.id == "development" or len(builders) >= 2)


def add_division_leads(tree: Tree, roles: list[RoleSpec]) -> list[RoleSpec]:
    """Bring in each division's lead when that division has build work to split."""
    chosen = {r.id: r for r in roles}
    for division_id in dict.fromkeys(r.division for r in roles):
        division = tree.units.get(division_id)
        if division is None or division.lead is None:
            continue
        builders = [r for r in roles if r.division == division_id and r.kind in ("builder", "repairer")]
        if division_needs_lead(division, builders):
            chosen.setdefault(division.lead, tree.by_id[division.lead])
    return list(chosen.values())


def cap_kind(role: RoleSpec) -> str:
    """The cap bucket a role counts against."""
    if role.kind in ("builder", "repairer"):
        return "builder" if role.division == "development" or role.kind == "repairer" else "support"
    return str(role.kind)


def dominant_languages(files: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for rel in files:
        builder = LANGUAGE_BUILDERS.get(PurePosixPath(rel).suffix.lower())
        if builder:
            counts[builder] = counts.get(builder, 0) + 1
    return [b for b, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


@dataclass
class Team:
    roles: list[RoleSpec]
    rationale: str
    standby: list[RoleSpec] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def ids(self) -> list[str]:
        return [r.id for r in self.roles]

    def of_kind(self, *kinds: str) -> list[RoleSpec]:
        return [r for r in self.roles if r.kind in kinds]


class TeamAssembler:
    def __init__(
        self, router: ModelRouter | None = None, *, tree: Tree | None = None, use_director_llm: bool = True,
    ) -> None:
        self.router = router
        self.tree = tree or default_tree()
        self.use_director_llm = use_director_llm and router is not None

    @property
    def _roles(self) -> dict[str, RoleSpec]:
        return {r.id: r for r in self.tree.roster}

    # -------------------------------------------------------------- heuristic
    def heuristic(self, request: CodingRequest, workspace_files: list[str]) -> Team:
        roles = self._roles
        text = " ".join([request.task, *request.constraints, *request.acceptance_criteria])
        scores = score_units(text, self.tree.roster)
        requested = self._validate_requested(request.requested_roles)
        always = self._always()
        chosen: dict[str, RoleSpec] = {rid: roles[rid] for rid in always}
        order = {r.id: i for i, r in enumerate(self.tree.roster)}

        def eligible(role: RoleSpec) -> bool:
            return not role.requires_explicit_request or role.id in requested or role.id in scores

        def top(bucket: str, cap: int) -> list[RoleSpec]:
            pool = [roles[rid] for rid in sorted(scores, key=lambda r: (-scores[r], order[r]))
                    if cap_kind(roles[rid]) == bucket and roles[rid].kind != "repairer"
                    and rid not in chosen and eligible(roles[rid])]
            return pool[:cap]

        has_code = any(not f.lower().endswith((".md", ".txt")) for f in workspace_files)
        if has_code and "codebase_analyst" in roles:
            chosen["codebase_analyst"] = roles["codebase_analyst"]
        for kind in ("planner", "designer", "reviewer", "support"):
            for role in top(kind, CAPS[kind]):
                chosen[role.id] = role
        if not any(r.kind == "planner" and r.id != self.tree.root.lead for r in chosen.values()) \
                and len(request.task.split()) > 40 and "requirements_analyst" in roles:
            chosen["requirements_analyst"] = roles["requirements_analyst"]
        if not has_code and not any(r.kind == "designer" for r in chosen.values()) and "software_architect" in roles:
            chosen["software_architect"] = roles["software_architect"]
        if request.acceptance_criteria and not any(r.kind == "reviewer" for r in chosen.values()) \
                and "qa_lead" in roles:
            chosen["qa_lead"] = roles["qa_lead"]

        builders = top("builder", CAPS["builder"])
        if scores.get("debugging_specialist") and has_code and "debugging_specialist" in roles:
            builders = [roles["debugging_specialist"], *builders][: CAPS["builder"]]
        if not any(b.id in LANGUAGE_BUILDERS.values() or b.id == FALLBACK_BUILDER for b in builders):
            languages = [b for b in dominant_languages(workspace_files) if b in roles]
            fallback = languages[0] if languages else FALLBACK_BUILDER
            if fallback in roles:
                primary = roles[fallback]
                builders = [primary, *[b for b in builders if b.id != primary.id]][: CAPS["builder"]]
        for role in builders:
            chosen[role.id] = role
        for rid in requested:
            chosen[rid] = roles[rid]

        protected = set(requested) | set(always)
        result = self._order(list(chosen.values()))
        result = self._cap(result, request.max_team_size or DEFAULT_MAX_TEAM, protected=protected)
        result = self._order(add_division_leads(self.tree, result))
        picked = ", ".join(r.name for r in result)
        return Team(result, f"Keyword and workspace match: {picked}.", self._standby(result), scores)

    # ---------------------------------------------------------------- division
    def for_division(self, request: CodingRequest, workspace_files: list[str], division: DivisionSpec) -> Team:
        """Members for a division called directly (no model call)."""
        members = {m.id: m for m in self.tree.children(division)}
        text = " ".join([request.task, *request.constraints, *request.acceptance_criteria])
        scores = score_units(text, list(members.values()))
        requested = self._validate_requested(request.requested_roles)
        outside = [rid for rid in requested if rid not in members]
        if outside:
            raise ValueError(f"requested roles are not in the {division.id} division: {outside}")
        eligible = [members[rid] for rid in scores]
        if division.id == "development" and not any(r.kind == "builder" for r in eligible):
            languages = [b for b in dominant_languages(workspace_files) if b in members]
            fallback = languages[0] if languages else FALLBACK_BUILDER
            if fallback in members:
                eligible.append(members[fallback])
        if not eligible:
            eligible = [members[rid] for rid in division.default_members]
        if not eligible and division.lead in members:
            eligible = [members[division.lead]]
        chosen = [*eligible, *[members[rid] for rid in requested]]
        chosen = list({r.id: r for r in sorted(chosen, key=lambda r: -scores.get(r.id, 0))}.values())
        chosen = self._per_kind_caps(chosen, protected=set(requested))
        chosen = self._cap(chosen, request.max_team_size or DEFAULT_MAX_TEAM, protected=set(requested))
        chosen = self._order(add_division_leads(self.tree, chosen))
        picked = ", ".join(r.name for r in chosen)
        return Team(chosen, f"{division.name} division, matched: {picked}.", [], scores)

    # ---------------------------------------------------------------- director
    def assemble(self, request: CodingRequest, workspace_files: list[str]) -> Team:
        base = self.heuristic(request, workspace_files)
        if not self.use_director_llm:
            return base
        try:
            refined = self._director(request, workspace_files, base)
        except (LLMError, ValueError) as exc:
            LOGGER.warning("Director team selection failed, using heuristic team: %s", exc)
            return base
        return refined or base

    def _director(self, request: CodingRequest, workspace_files: list[str], base: Team) -> Team | None:
        assert self.router is not None
        roles = self._roles
        lines = []
        for r in self.tree.roster:
            team = f" (team: {', '.join(r.members)})" if r.is_team else ""
            lines.append(f"- {r.id}: {r.name} [{r.kind}] {r.responsibility}{team}")
        prompt = (
            f"# Request\n{request.task}\n\n"
            + (f"Constraints: {'; '.join(request.constraints)}\n" if request.constraints else "")
            + (f"Acceptance criteria: {'; '.join(request.acceptance_criteria)}\n" if request.acceptance_criteria else "")
            + f"\nWorkspace has {len(workspace_files)} files"
            + (f", e.g. {', '.join(workspace_files[:15])}" if workspace_files else " (new project)")
            + "\n\n# Specialists\n" + "\n".join(lines) + f"\n\n# Suggested team\n{', '.join(base.ids)}\n\n"
            f"Return the final team (max {request.max_team_size or DEFAULT_MAX_TEAM} specialists). "
            "Keep the suggestion unless a specialist is clearly missing or clearly unnecessary."
        )
        director = self.tree.by_id[self.tree.root.lead]
        model, declared_by = self.tree.effective_model(director)
        client, _ = self.router.resolve("planning", model, declared_by)
        reply = client.generate_json(DIRECTOR_SYSTEM, prompt, schema=TEAM_SCHEMA)
        ids = reply.get("team")
        if not isinstance(ids, list):
            raise ValueError("director reply has no team list")
        requested = set(self._validate_requested(request.requested_roles))
        always = self._always()
        chosen: dict[str, RoleSpec] = {rid: roles[rid] for rid in always}
        for rid in ids:
            role = roles.get(str(rid).strip())
            if role is None:
                continue
            if role.requires_explicit_request and role.id not in requested and role.id not in base.scores:
                continue
            chosen[role.id] = role
        for rid in requested:
            chosen[rid] = roles[rid]
        if not any(cap_kind(r) == "builder" for r in chosen.values()):
            for role in base.roles:
                if cap_kind(role) == "builder":
                    chosen[role.id] = role
        protected = requested | set(always)
        result = self._order(list(chosen.values()))
        result = self._per_kind_caps(result, protected=protected)
        result = self._cap(result, request.max_team_size or DEFAULT_MAX_TEAM, protected=protected)
        result = self._order(add_division_leads(self.tree, result))
        rationale = str(reply.get("rationale") or "").strip() or base.rationale
        return Team(result, rationale, self._standby(result), base.scores)

    # ----------------------------------------------------------------- helpers
    def _always(self) -> list[str]:
        roles = self._roles
        return [rid for rid in (self.tree.root.lead, "tech_lead") if rid in roles]

    def _validate_requested(self, requested: list[str]) -> list[str]:
        roles = self._roles
        unknown = [rid for rid in requested if rid not in roles]
        if unknown:
            raise ValueError(f"unknown requested roles: {unknown}")
        return list(dict.fromkeys(requested))

    def _order(self, roles: list[RoleSpec]) -> list[RoleSpec]:
        index = {r.id: i for i, r in enumerate(self.tree.roster)}
        return sorted(roles, key=lambda r: (PHASE_ORDER[r.phase], index.get(r.id, 999)))

    @staticmethod
    def _per_kind_caps(roles: list[RoleSpec], protected: set[str]) -> list[RoleSpec]:
        kept, counts = [], {}
        for role in roles:
            kind = cap_kind(role)
            cap = CAPS.get(kind)
            if cap is not None and role.id not in protected and counts.get(kind, 0) >= cap:
                continue
            counts[kind] = counts.get(kind, 0) + 1
            kept.append(role)
        return kept

    @staticmethod
    def _cap(roles: list[RoleSpec], limit: int, protected: set[str]) -> list[RoleSpec]:
        limit = max(limit, 1)
        roles = list(roles)
        for kind in TRIM_ORDER:
            while len(roles) > limit:
                victims = [r for r in roles if cap_kind(r) == kind and r.id not in protected]
                if kind == "builder" and len([r for r in roles if cap_kind(r) == "builder"]) <= 1:
                    break
                if not victims:
                    break
                roles.remove(victims[-1])
        return roles

    def _standby(self, roles: list[RoleSpec]) -> list[RoleSpec]:
        ids = {r.id for r in roles}
        return [self.tree.by_id[rid] for rid in STANDBY if rid in self.tree.by_id and rid not in ids]
