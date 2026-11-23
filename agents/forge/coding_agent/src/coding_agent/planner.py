
"""Turn coding tasks into structured implementation plans using an LLM."""

from __future__ import annotations

import json
import re

from coding_agent.llm_client import OllamaClient
from coding_agent.engineering_spec import EngineeringSpecification
from coding_agent.models import (
    ChangeType,
    CodingTask,
    FileChange,
    ImplementationPlan,
)


class PlanningError(RuntimeError):
    """Raised when the LLM cannot produce a valid implementation plan."""


def _normalize_model_file_content(path: str, content: str | None) -> str | None:
    """Repair a common double-escaped multiline response without rewriting
    ordinary string-literal escapes in otherwise single-line source.

    Ollama occasionally returns file content containing literal ``\\n``
    separators after JSON decoding. Only normalize when the content has no
    physical line breaks and an escaped newline is followed by indentation,
    which is a strong signal that the model intended a multiline block.
    """

    if content is None:
        return None

    has_physical_newline = "\n" in content or "\r" in content
    has_indented_escaped_line = re.search(r"\\n[ \t]+", content) is not None

    if not has_physical_newline and has_indented_escaped_line:
        content = (
            content.replace(r"\r\n", "\n")
            .replace(r"\n", "\n")
            .replace(r"\r", "\n")
        )

    if path.casefold().endswith((".py", ".pyi")):
        try:
            compile(content, path, "exec")
        except SyntaxError as exc:
            raise ValueError(
                f"Generated Python content for {path!r} is not syntactically "
                f"valid: {exc.msg} (line {exc.lineno}). The plan was rejected "
                "before approval; no workspace files were changed."
            ) from exc

    return content


class CodingPlanner:
    """Generate validated implementation plans without modifying files."""

    def __init__(self, client: OllamaClient | None = None) -> None:
        self.client = client or OllamaClient()

    @staticmethod
    def _domain_artifact_guidance(specification: EngineeringSpecification | None) -> str:
        if specification is None:
            return ""
        paths = {unit.target_path.replace("\\", "/").casefold() for unit in specification.implementation_units}
        requirement_text = " ".join(
            " ".join((item.description, *item.acceptance_criteria))
            for item in specification.requirements
        ).casefold()
        sections: list[str] = []
        if any(path.endswith(("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")) for path in paths) and any(
            token in requirement_text for token in ("postgres", "postgresql", "named volume", "health check")
        ):
            sections.append(
                "DOCKER COMPOSE REFERENCE SHAPE (adapt to the exact ESpec; do not omit required keys):\n"
                "services:\n"
                "  backend:\n"
                "    build: ./backend\n"
                "    depends_on:\n"
                "      postgres:\n"
                "        condition: service_healthy\n"
                "  frontend:\n"
                "    build: ./frontend\n"
                "    depends_on:\n"
                "      backend:\n"
                "        condition: service_started\n"
                "  postgres:\n"
                "    image: postgres:17\n"
                "    environment:\n"
                "      POSTGRES_DB: ${POSTGRES_DB:?Set POSTGRES_DB in .env}\n"
                "      POSTGRES_USER: ${POSTGRES_USER:?Set POSTGRES_USER in .env}\n"
                "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}\n"
                "    volumes:\n"
                "      - postgres_data:/var/lib/postgresql/data\n"
                "    healthcheck:\n"
                "      test: [\\\"CMD-SHELL\\\", \\\"pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}\\\"]\n"
                "      interval: 10s\n"
                "      timeout: 5s\n"
                "      retries: 5\n"
                "volumes:\n"
                "  postgres_data:\n"
                "IMPORTANT: the top-level volumes: mapping is aligned with services:, not nested under it. The frontend uses service_started unless a real frontend/backend healthcheck is also defined. Do not invent health endpoints."
            )
        if ".env.example" in paths:
            sections.append(
                "ENVIRONMENT EXAMPLE REFERENCE (comment every setting; never put real secrets here):\n"
                "# Database name used by PostgreSQL\nPOSTGRES_DB=fafas_burger\n"
                "# Application database user\nPOSTGRES_USER=fafas_app\n"
                "# Safe non-secret placeholder only; replace in untracked .env\nPOSTGRES_PASSWORD=change_me\n"
                "# PostgreSQL service DNS name on Compose network\nPOSTGRES_HOST=postgres\n"
                "# PostgreSQL container port\nPOSTGRES_PORT=5432\n"
                "Use non-secret local values directly; only secret-bearing keys need placeholder values."
            )
        if "project.yaml" in paths:
            sections.append(
                "PROJECT METADATA REFERENCE (use only project details supported by the task):\n"
                "goal: Build a polished, realistic burger-shop web application for customer browsing/ordering and staff administration.\n"
                "approved_stack:\n"
                "  frontend: React + TypeScript + Vite\n"
                "  backend: Python + Django + Django REST Framework\n"
                "  database: PostgreSQL\n"
                "  orchestration: Docker Compose\n"
                "repository_architecture:\n"
                "  frontend: Customer-facing React application\n"
                "  backend: Django REST API\n"
                "  postgres: PostgreSQL persistence\n"
                "phases:\n"
                "  - id: A\n    name: Phase A\n"
                "  - id: B\n    name: Phase B\n"
                "  - id: C\n    name: Phase C\n"
                "  - id: D\n    name: Phase D\n"
                "  - id: E\n    name: Phase E\n"
                "  - id: F\n    name: Phase F\n"
                "acceptance_status:\n"
                "  phase_1: in_progress\n"
                "  overall: in_progress\n"
                "The task provides phase IDs A-F but not all phase titles. Do not invent phase names; use neutral Phase A through Phase F labels unless titles are explicitly supplied."
            )
        return "\n\n".join(sections)

    def plan(
        self,
        task: CodingTask,
        context: str = "",
        specification: EngineeringSpecification | None = None,
    ) -> ImplementationPlan:
        """Ask the LLM to propose changes for a coding task."""

        implementation_file_groups: dict[str, list] = {}
        if specification is not None:
            for unit in specification.implementation_units:
                implementation_file_groups.setdefault(unit.target_path, []).append(unit)

        requirement_by_id = (
            {requirement.requirement_id: requirement for requirement in specification.requirements}
            if specification is not None
            else {}
        )
        implementation_group_sections = []
        directory_targets = set()
        if specification is not None:
            import re
            for requirement in specification.requirements:
                if requirement.kind.value != "constraint" and re.search(r"\b(?:directory|directories|folder|folders|layout|structure)\b", requirement.description + " " + " ".join(requirement.acceptance_criteria), re.I):
                    text = requirement.description + " " + " ".join(requirement.acceptance_criteria)
                    directory_targets.update(
                        match.rstrip("/") for match in re.findall(
                            r"(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.-]+/)+)", text
                        )
                        if match.strip("/").casefold() not in {"root", "."}
                        and all(part not in {"", ".", ".."} for part in match.rstrip("/").split("/"))
                        and ":" not in match.split("/", 1)[0]
                    )
        for path, units in sorted(implementation_file_groups.items()):
            normalized_group_path = path.replace("\\", "/").rstrip("/")
            group_kind = "DIRECTORY" if normalized_group_path in directory_targets else "FILE"
            lines = [f"- {group_kind}: {normalized_group_path}"]
            for unit in units:
                lines.append(
                    f"  UNIT: {unit.unit_id} — {unit.title} "
                    f"(requirements: {', '.join(unit.satisfies) or 'none'})"
                )
                for criterion in unit.acceptance_criteria:
                    lines.append(f"    UNIT ACCEPTANCE: {criterion}")
                for requirement_id in unit.satisfies:
                    requirement = requirement_by_id.get(requirement_id)
                    if requirement is None:
                        continue
                    lines.append(
                        f"    REQUIREMENT {requirement_id}: {requirement.description}"
                    )
                    for criterion in requirement.acceptance_criteria:
                        lines.append(f"      REQUIRED RESULT: {criterion}")
            implementation_group_sections.append("\n".join(lines))

        implementation_file_groups_text = (
            "\n".join(implementation_group_sections)
            if implementation_group_sections
            else "- (No implementation units.)"
        )

        validation_marker = "ENGINEERING SPECIFICATION PLAN VALIDATION FAILURE"
        validation_feedback = ""
        existing_context = context
        if validation_marker in context:
            existing_context, _, validation_feedback = context.partition(validation_marker)
            validation_feedback = validation_marker + validation_feedback

        domain_guidance = self._domain_artifact_guidance(specification)
        directory_obligations = []
        if specification is not None:
            import re
            for requirement in specification.requirements:
                text = " ".join((requirement.description, *requirement.acceptance_criteria))
                if requirement.kind.value != "constraint" and re.search(r"\b(?:directory|directories|folder|folders|layout|structure)\b", text, re.I):
                    paths = [
                        path for path in re.findall(
                            r"(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.-]+/)+)", text
                        ) if path.strip("/").casefold() not in {"root", "."}
                        and all(part not in {"", ".", ".."} for part in path.rstrip("/").split("/"))
                        and ":" not in path.split("/", 1)[0]
                    ]
                    if paths:
                        directory_obligations.append(
                            f"- {requirement.requirement_id}: " + ", ".join(dict.fromkeys(path.rstrip("/") for path in paths))
                        )
        directory_obligations_text = "\n".join(directory_obligations) or "- (No explicit directory-creation obligations.)"

        prompt = f"""
Create an implementation plan for this software engineering task.

TASK:
{task.description}

TASK ID:
{task.task_id}

WORKSPACE:
{task.workspace}

CONSTRAINTS:
{json.dumps(task.constraints)}

ACCEPTANCE CRITERIA:
{json.dumps(task.acceptance_criteria)}

ENGINEERING SPECIFICATION (AUTHORITATIVE REQUIREMENTS AND DESIGN):
{specification.to_json() if specification is not None else "(Not supplied.)"}

EXISTING CODE CONTEXT:
{existing_context if existing_context.strip() else "(No existing code provided.)"}

VALIDATION FEEDBACK (when present, treat as mandatory corrections; preserve the previous draft's valid content):
{validation_feedback if validation_feedback.strip() else "(No validation correction requested.)"}

DOMAIN-SPECIFIC ARTIFACT GUIDANCE:
{domain_guidance if domain_guidance else "(No specialized artifact template applies.)"}

IMPLEMENTATION COVERAGE CONTRACT:
- Treat the Engineering Specification and explicit directory obligations as authoritative. For FILE units, propose a file change. For DIRECTORY units and every missing explicit directory listed above, propose change_type "mkdir" at that exact path, with content null. Directory operations are distinct from file changes.
- Every implementation unit is mandatory.
- Group implementation units by target_path and create exactly one operation for each unique target_path. A DIRECTORY target uses mkdir; a FILE target uses create/modify/delete.
- When multiple units share the same target_path, that single operation must implement the combined acceptance criteria and contract obligations of ALL units in that path group. One change DOES cover all units in the same-path group when its content satisfies their combined criteria.
- Units with different target_path values are separate obligations and each requires its own planned operation at that exact path.
- Never create duplicate changes for the same path.
- Functional and non-functional requirements must be covered by all implementation units mapped to them; same-path units are implemented together as one operation group. Every explicit directory obligation must have its own mkdir operation unless that directory already exists.
IMPLEMENTATION TARGET GROUPS (one operation per listed path):
{implementation_file_groups_text}

EXPLICIT DIRECTORY OBLIGATIONS (each requires a mkdir operation unless already present in the workspace):
{directory_obligations_text}

ARTIFACT CONTENT CONTRACT:
- The target groups above repeat the exact acceptance obligations for each artifact. Treat every REQUIRED RESULT and UNIT ACCEPTANCE line as a mandatory content requirement.
- Do not return empty, skeletal, placeholder-only, or metadata-only content when the acceptance criteria require substantive configuration or documentation.
- For configuration files, include the complete required sections, values, references, and cross-file consistency. For project metadata, enumerate each required item rather than merely asserting that it exists.
- Before returning JSON, mentally audit every file's complete content against every acceptance line for that file. A path being present in changes does not mean its requirement is implemented.
- Preserve correct content when correcting an operation type; do not simplify or remove valid content.
- A constraint with no implementation-unit mapping is a compliance boundary, not a request to create or modify a file merely to demonstrate compliance. Respect it in every relevant change and follow its verification criteria.
- If a constraint is explicitly mapped to implementation units, those units are mandatory and must be implemented.
- Only contracts referenced by implementation units are implementation obligations. Unreferenced contracts are context/verification descriptions and must not create extra file changes.
- Do not invent additional files or units to make coverage appear complete. If the ESpec has an inconsistency, explain it in risks rather than silently changing its scope.

For an explicitly required directory, represent the operation as:
{{"path": "frontend", "change_type": "mkdir", "reason": "Create required directory", "content": null}}

Return one JSON object with this exact structure:
{{
  "summary": "Brief description of the solution",
  "changes": [
    {{
      "path": "workspace-relative/path.py",
      "change_type": "create",
      "reason": "Why this change is necessary",
      "content": "Complete file content as a string"
    }}
  ],
  "verification_commands": [],
  "requires_approval": true,
  "risks": []
}}

RULES:
- Only propose workspace-relative paths. Never use ".". Use mkdir only for an explicitly required DIRECTORY target; content must be null. Create each parent directory before its child directory. Never use mkdir for a file.
- Treat WORKSPACE FILE INVENTORY as the authoritative list of currently existing eligible workspace files.
- If a proposed path appears in WORKSPACE FILE INVENTORY, use modify (or delete when removal is explicitly required); never use create for an existing path.
- If a proposed path is absent from WORKSPACE FILE INVENTORY, use create only when it is genuinely a new file. Inventory may be bounded; if marked truncated, do not assume an absent path is missing. Request/inspect additional context rather than guessing.
- Existing source files omitted from SOURCE FILES still exist; omission means their contents were not selected, not that they are absent.
- Prefer modifying existing files over creating duplicate alternatives.
- Create and modify operations require complete file content.
- Delete and mkdir operations must use content null. A mkdir operation creates exactly one directory; its parent must already exist or be created by an earlier mkdir change.
- Do not propose the same path more than once.
- Include complete content for every created or modified file.
- Prefer simple, maintainable solutions.
- Do not claim tests passed; you are only proposing a plan.
- Verification commands are suggestions only; deterministic checks are added by the orchestrator.
- When acceptance criteria are supplied, include focused behavioral tests where practical, preferably as workspace-relative test files in this plan.
- If this plan creates pytest-style test files (test_*.py or *_test.py), suggest the standard pytest command; the verifier will select it only when tests exist after planning.
- Never claim behavioral correctness based only on syntax compilation.
- Do not modify unrelated files.
- When an Engineering Specification is supplied, implement every implementation unit under its declared target_path and use only paths declared by its architecture modules or implementation units.
- Honor all constraint requirements, but do not create file changes solely for constraints that have no implementation-unit mapping.
- Do not omit any implementation unit or any functional/non-functional implementation requirement.
- Set requires_approval to true.
- Return JSON only.
"""

        try:
            response = self.client.generate_json(prompt)

            summary = response["summary"]
            raw_changes = response["changes"]

            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("summary must be a non-empty string")

            if not isinstance(raw_changes, list) or not raw_changes:
                raise ValueError("changes must be a non-empty list")

            def parse_change(item):
                change_type = ChangeType(item["change_type"])
                raw_content = item.get("content")
                if change_type in (ChangeType.DELETE, ChangeType.MKDIR):
                    if raw_content is not None:
                        raise ValueError(
                            f"{change_type.value} operation for {item.get('path')!r} must use content null"
                        )
                    content = None
                else:
                    content = _normalize_model_file_content(
                        item["path"], raw_content
                    )
                return FileChange(
                    path=item["path"],
                    change_type=change_type,
                    reason=item["reason"],
                    content=content,
                )

            changes = tuple(parse_change(item) for item in raw_changes)

            raw_commands = response.get("verification_commands", [])
            if not isinstance(raw_commands, list):
                raise ValueError("verification_commands must be a list")

            commands = tuple(
                tuple(command) for command in raw_commands
            )

            raw_risks = response.get("risks", [])
            if not isinstance(raw_risks, list):
                raise ValueError("risks must be a list")

            requires_approval = response.get("requires_approval", True)
            if not isinstance(requires_approval, bool):
                raise ValueError("requires_approval must be a boolean")

            if not requires_approval:
                raise ValueError("Generated plans must require approval")

            return ImplementationPlan(
                task_id=task.task_id,
                summary=summary,
                changes=changes,
                verification_commands=commands,
                requires_approval=True,
                risks=tuple(str(risk) for risk in raw_risks),
            )

        except (KeyError, TypeError, ValueError) as exc:
            raise PlanningError(
                f"Could not validate the generated implementation plan: {exc}"
            ) from exc
