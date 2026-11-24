"""Team selection: pick a proportionate team for one request.

At the agent level the Engineering Director (the agent's lead) owns this step.
When a single division is called directly, ``for_division`` picks members from
that division only, without a model call. A deterministic pass scores every
role's triggers against the request and the workspace, then the Director model
may refine the shortlist. Whatever the model says is validated against the
roster and the size caps, and the deterministic team is the fallback.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .llm import LLMError, ModelRouter
from .models import CodingRequest
from .registry import ROLES, ROSTER, DivisionSpec, RoleSpec, get_division
from .prompts import DIRECTOR_SYSTEM, TEAM_SCHEMA

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_TEAM = 10
# "builder" counts core developers (Development division); "support" counts
# builders from QA, DevOps and Documentation (tests, pipelines, docs).
CAPS = {"analyst": 1, "planner": 2, "designer": 2, "builder": 3, "support": 2, "reviewer": 2}
# When trimming to the size cap, drop the least essential kinds first.
TRIM_ORDER = ("reviewer", "planner", "support", "designer", "analyst", "builder")
PHASE_ORDER = {"analyze": 0, "plan": 1, "design": 2, "build": 3, "review": 4}
ALWAYS = ("engineering_director", "tech_lead")
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


def division_needs_lead(division: DivisionSpec, builders: list[RoleSpec]) -> bool:
    """A division lead splits the work when there is something to split."""
    return bool(builders) and (division.id == "development" or len(builders) >= 2)


def add_division_leads(roles: list[RoleSpec]) -> list[RoleSpec]:
    """Bring in each division's lead when that division has build work to split."""
    chosen = {r.id: r for r in roles}
    for division_id in dict.fromkeys(r.division for r in roles):
        division = get_division(division_id)
        builders = [r for r in roles if r.division == division_id and r.kind in ("builder", "repairer")]
        if division_needs_lead(division, builders):
            chosen.setdefault(division.lead, ROLES[division.lead])
    return list(chosen.values())


def cap_kind(role: RoleSpec) -> str:
    """The cap bucket a role counts against."""
    if role.kind in ("builder", "repairer"):
        return "builder" if role.division == "development" or role.kind == "repairer" else "support"
    return role.kind


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


# Triggers that are word stems ("integrat" -> integrate, integration) match as
# prefixes; every other alphanumeric trigger must match a whole word (plurals ok).
_STEMS = frozenset({"integrat", "scalab", "optimi", "vulnerab", "dependenc", "deprecat"})


def _trigger_hits(trigger: str, text: str) -> bool:
    if trigger != trigger.strip() or not re.fullmatch(r"[a-z0-9 ]+", trigger):
        return trigger in text  # punctuation-bearing triggers: ".py", "c#", "go "
    if trigger in _STEMS:
        return re.search(rf"\b{re.escape(trigger)}", text) is not None
    return re.search(rf"\b{re.escape(trigger)}(?:s|es)?\b", text) is not None


def score_roles(text: str) -> dict[str, float]:
    lowered = f" {text.lower()} "
    scores: dict[str, float] = {}
    for role in ROSTER:
        hits = [t for t in role.triggers if _trigger_hits(t, lowered)]
        if hits:
            scores[role.id] = float(len(hits))
    # A language-specific developer covers the generic one for the same layer.
    for generic, specific in SUBSUMED_BY.items():
        if generic in scores and any(s in scores for s in specific):
            del scores[generic]
    return scores


def dominant_languages(files: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for rel in files:
        builder = LANGUAGE_BUILDERS.get(PurePosixPath(rel).suffix.lower())
        if builder:
            counts[builder] = counts.get(builder, 0) + 1
    return [b for b, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


class TeamAssembler:
    def __init__(self, router: ModelRouter | None = None, *, use_director_llm: bool = True) -> None:
        self.router = router
        self.use_director_llm = use_director_llm and router is not None

    # -------------------------------------------------------------- heuristic
    def heuristic(self, request: CodingRequest, workspace_files: list[str]) -> Team:
        text = " ".join([request.task, *request.constraints, *request.acceptance_criteria])
        scores = score_roles(text)
        requested = self._validate_requested(request.requested_roles)
        chosen: dict[str, RoleSpec] = {rid: ROLES[rid] for rid in ALWAYS}

        def eligible(role: RoleSpec) -> bool:
            return not role.requires_explicit_request or role.id in requested or role.id in scores

        def top(bucket: str, cap: int) -> list[RoleSpec]:
            order = {r.id: i for i, r in enumerate(ROSTER)}
            pool = [ROLES[rid] for rid in sorted(scores, key=lambda r: (-scores[r], order[r]))
                    if cap_kind(ROLES[rid]) == bucket and ROLES[rid].kind != "repairer"
                    and rid not in chosen and eligible(ROLES[rid])]
            return pool[:cap]

        has_code = any(not f.lower().endswith((".md", ".txt")) for f in workspace_files)
        if has_code:
            chosen["codebase_analyst"] = ROLES["codebase_analyst"]
        for kind in ("planner", "designer", "reviewer", "support"):
            for role in top(kind, CAPS[kind]):
                chosen[role.id] = role
        if not any(r.kind == "planner" and r.id != "engineering_director" for r in chosen.values()) \
                and len(request.task.split()) > 40:
            chosen["requirements_analyst"] = ROLES["requirements_analyst"]
        if not has_code and not any(r.kind == "designer" for r in chosen.values()):
            chosen["software_architect"] = ROLES["software_architect"]
        if request.acceptance_criteria and not any(r.kind == "reviewer" for r in chosen.values()):
            chosen["qa_lead"] = ROLES["qa_lead"]

        builders = top("builder", CAPS["builder"])
        if scores.get("debugging_specialist") and has_code:
            builders = [ROLES["debugging_specialist"], *builders][: CAPS["builder"]]
        if not any(b.id in LANGUAGE_BUILDERS.values() or b.id == FALLBACK_BUILDER for b in builders):
            languages = dominant_languages(workspace_files)
            primary = ROLES[languages[0]] if languages else ROLES[FALLBACK_BUILDER]
            builders = [primary, *[b for b in builders if b.id != primary.id]][: CAPS["builder"]]
        for role in builders:
            chosen[role.id] = role
        for rid in requested:
            chosen[rid] = ROLES[rid]

        roles = self._order(list(chosen.values()))
        roles = self._cap(roles, request.max_team_size or DEFAULT_MAX_TEAM, protected=set(requested) | set(ALWAYS))
        roles = self._order(add_division_leads(roles))
        picked = ", ".join(r.name for r in roles)
        return Team(roles, f"Keyword and workspace match: {picked}.", self._standby(roles), scores)

    # ---------------------------------------------------------------- division
    def for_division(self, request: CodingRequest, workspace_files: list[str], division: DivisionSpec) -> Team:
        """Members for a division called directly (no model call)."""
        text = " ".join([request.task, *request.constraints, *request.acceptance_criteria])
        members = set(division.members)
        scores = {rid: s for rid, s in score_roles(text).items() if rid in members}
        requested = self._validate_requested(request.requested_roles)
        outside = [rid for rid in requested if rid not in members]
        if outside:
            raise ValueError(f"requested roles are not in the {division.id} division: {outside}")
        eligible = [ROLES[rid] for rid in scores
                    if not ROLES[rid].requires_explicit_request or rid in requested or rid in scores]
        if division.id == "development" and not any(r.kind == "builder" for r in eligible):
            languages = [b for b in dominant_languages(workspace_files) if b in members]
            eligible.append(ROLES[languages[0] if languages else FALLBACK_BUILDER])
        if not eligible:
            eligible = [ROLES[rid] for rid in division.default_members]
        if not eligible:
            eligible = [ROLES[division.lead]]
        roles = [*eligible, *[ROLES[rid] for rid in requested]]
        roles = list({r.id: r for r in sorted(roles, key=lambda r: -scores.get(r.id, 0))}.values())
        roles = self._per_kind_caps(roles, protected=set(requested))
        roles = self._cap(roles, request.max_team_size or DEFAULT_MAX_TEAM, protected=set(requested))
        roles = self._order(add_division_leads(roles))
        picked = ", ".join(r.name for r in roles)
        return Team(roles, f"{division.name} division, matched: {picked}.", [], scores)

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
        roster_lines = "\n".join(f"- {r.id}: {r.name} [{r.kind}] {r.responsibility}" for r in ROSTER)
        prompt = (
            f"# Request\n{request.task}\n\n"
            + (f"Constraints: {'; '.join(request.constraints)}\n" if request.constraints else "")
            + (f"Acceptance criteria: {'; '.join(request.acceptance_criteria)}\n" if request.acceptance_criteria else "")
            + f"\nWorkspace has {len(workspace_files)} files"
            + (f", e.g. {', '.join(workspace_files[:15])}" if workspace_files else " (new project)")
            + f"\n\n# Roster\n{roster_lines}\n\n# Suggested team\n{', '.join(base.ids)}\n\n"
            f"Return the final team (max {request.max_team_size or DEFAULT_MAX_TEAM} roles). "
            "Keep the suggestion unless a role is clearly missing or clearly unnecessary."
        )
        reply = self.router.client_for("planning").generate_json(DIRECTOR_SYSTEM, prompt, schema=TEAM_SCHEMA)
        ids = reply.get("team")
        if not isinstance(ids, list):
            raise ValueError("director reply has no team list")
        requested = set(self._validate_requested(request.requested_roles))
        chosen: dict[str, RoleSpec] = {rid: ROLES[rid] for rid in ALWAYS}
        for rid in ids:
            role = ROLES.get(str(rid).strip())
            if role is None:
                continue
            if role.requires_explicit_request and role.id not in requested and role.id not in base.scores:
                continue
            chosen[role.id] = role
        for rid in requested:
            chosen[rid] = ROLES[rid]
        if not any(cap_kind(r) == "builder" for r in chosen.values()):
            for role in base.roles:
                if cap_kind(role) != "builder":
                    continue
                chosen[role.id] = role
        roles = self._order(list(chosen.values()))
        roles = self._per_kind_caps(roles, protected=requested | set(ALWAYS))
        roles = self._cap(roles, request.max_team_size or DEFAULT_MAX_TEAM, protected=requested | set(ALWAYS))
        roles = self._order(add_division_leads(roles))
        rationale = str(reply.get("rationale") or "").strip() or base.rationale
        return Team(roles, rationale, self._standby(roles), base.scores)

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _validate_requested(requested: list[str]) -> list[str]:
        unknown = [rid for rid in requested if rid not in ROLES]
        if unknown:
            raise ValueError(f"unknown requested roles: {unknown}")
        return list(dict.fromkeys(requested))

    @staticmethod
    def _order(roles: list[RoleSpec]) -> list[RoleSpec]:
        index = {r.id: i for i, r in enumerate(ROSTER)}
        return sorted(roles, key=lambda r: (PHASE_ORDER[r.phase], index[r.id]))

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

    @staticmethod
    def _standby(roles: list[RoleSpec]) -> list[RoleSpec]:
        ids = {r.id for r in roles}
        return [ROLES[rid] for rid in STANDBY if rid not in ids]
