
"""Turn coding tasks into structured implementation plans using an LLM."""

from __future__ import annotations

import json
import logging
import re

from coding_agent.llm_client import OllamaClient
from coding_agent.engineering_spec import EngineeringSpecification
from coding_agent.models import (
    ChangeType,
    CodingTask,
    FileChange,
    ImplementationPlan,
)
from coding_agent.specification_checks import (
    CheckFailure,
    ensure_postgres_pg_isready_healthcheck,
)


LOGGER = logging.getLogger(__name__)


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


    @staticmethod
    def _normalized_plan_path(path: str) -> str:
        return path.replace("\\", "/").strip("/").casefold()

    # Bound on how much of each already-repaired file is shown to later calls.
    _REPAIRED_FILE_CONTEXT_CHARS = 6000
    # Model calls allowed per file in a focused repair (first try plus one retry).
    _REPAIR_TRIES_PER_FILE = 2

    def repair_missing_changes(
        self,
        task: CodingTask,
        *,
        context: str,
        specification: EngineeringSpecification,
        existing_plan: ImplementationPlan,
        required_paths: tuple[str, ...],
        issues: tuple[str, ...],
    ) -> tuple[FileChange, ...]:
        """Repair ESpec file groups that failed plan validation, one file per model call.

        Small models asked for several files in one response often return only
        some of them or run out of output tokens. Each call here asks for exactly
        one target path. Files repaired earlier in this pass are added to the
        context so later files can use the names they define.
        """
        requested = tuple(dict.fromkeys(path.replace("\\", "/") for path in required_paths))
        if not requested:
            raise PlanningError("A focused ESpec repair requires at least one target path.")

        repaired: list[FileChange] = []
        for path in requested:
            sibling_context = ""
            if repaired:
                sibling_context = "\n\nFILES ALREADY REPAIRED IN THIS PASS (use their names and interfaces; do not return them):\n" + "\n\n".join(
                    f"FILE: {change.path}\n{(change.content or '')[:self._REPAIRED_FILE_CONTEXT_CHARS]}"
                    for change in repaired
                )
            # One extra try per file: a small model sometimes returns nothing,
            # or only other files, for a single-file request. The second call
            # is told exactly what was wrong; a second failure is final.
            retry_note = ""
            for repair_try in range(1, self._REPAIR_TRIES_PER_FILE + 1):
                try:
                    repaired.extend(
                        self._repair_file_group(
                            task,
                            context=context + sibling_context + retry_note,
                            specification=specification,
                            existing_plan=existing_plan,
                            required_paths=(path,),
                            issues=issues,
                            ignore_extra_paths=True,
                        )
                    )
                    break
                except PlanningError as exc:
                    if repair_try >= self._REPAIR_TRIES_PER_FILE:
                        raise
                    LOGGER.warning("Focused repair for %s was rejected; retrying once: %s", path, exc)
                    retry_note = (
                        f"\n\nYOUR PREVIOUS RESPONSE FOR {path} WAS REJECTED: {exc}\n"
                        f"Return a JSON object whose changes list contains exactly one FileChange "
                        f"with path \"{path}\" and its complete content."
                    )
        return tuple(repaired)

    def _repair_file_group(
        self,
        task: CodingTask,
        *,
        context: str,
        specification: EngineeringSpecification,
        existing_plan: ImplementationPlan,
        required_paths: tuple[str, ...],
        issues: tuple[str, ...],
        ignore_extra_paths: bool = False,
    ) -> tuple[FileChange, ...]:
        """Generate the requested ESpec file group in a single model call."""
        requested = tuple(dict.fromkeys(path.replace("\\", "/") for path in required_paths))
        if not requested:
            raise PlanningError("A focused ESpec repair requires at least one target path.")

        units_by_path: dict[str, list] = {}
        for unit in specification.implementation_units:
            units_by_path.setdefault(self._normalized_plan_path(unit.target_path), []).append(unit)
        requested_keys = {self._normalized_plan_path(path) for path in requested}
        unknown = requested_keys - set(units_by_path)
        if unknown:
            raise PlanningError(
                "Focused ESpec repair received paths without implementation units: "
                + ", ".join(sorted(unknown))
            )

        requirements_by_id = {
            requirement.requirement_id: requirement
            for requirement in specification.requirements
        }
        contracts_by_id = {
            contract.contract_id: contract
            for contract in specification.contracts
        }
        existing_by_path = {
            self._normalized_plan_path(change.path): change
            for change in existing_plan.changes
        }

        file_sections: list[str] = []
        for path in requested:
            path_key = self._normalized_plan_path(path)
            units = units_by_path[path_key]
            file_sections.append(f"TARGET FILE: {path}")
            for unit in units:
                file_sections.append(
                    f"UNIT {unit.unit_id}: {unit.title}\n"
                    f"kind: {unit.kind.value}\n"
                    f"depends_on: {json.dumps(unit.depends_on)}\n"
                    f"satisfies: {json.dumps(unit.satisfies)}\n"
                    f"implements: {json.dumps(unit.implements)}"
                )
                for criterion in unit.acceptance_criteria:
                    file_sections.append(f"UNIT ACCEPTANCE: {criterion}")
                for requirement_id in unit.satisfies:
                    requirement = requirements_by_id.get(requirement_id)
                    if requirement is None:
                        continue
                    file_sections.append(
                        f"REQUIREMENT {requirement_id}: {requirement.description}"
                    )
                    for criterion in requirement.acceptance_criteria:
                        file_sections.append(f"REQUIRED RESULT: {criterion}")
                for contract_id in unit.implements:
                    contract = contracts_by_id.get(contract_id)
                    if contract is not None:
                        file_sections.append(
                            f"CONTRACT {contract.contract_id}: {contract.name}; "
                            f"kind={getattr(contract.kind, 'value', contract.kind)}; "
                            f"inputs={json.dumps([item.name for item in contract.inputs])}; "
                            f"outputs={json.dumps([item.name for item in contract.outputs])}; "
                            f"invariants={json.dumps(contract.invariants)}"
                        )
            previous = existing_by_path.get(path_key)
            if previous is not None:
                file_sections.append(
                    "PREVIOUS DRAFT FOR THIS FILE (repair it; preserve valid details):\n"
                    + (previous.content or "<deleted>")
                )
            else:
                file_sections.append(
                    "PREVIOUS DRAFT FOR THIS FILE: <not present in the failed plan>"
                )
            file_sections.append("")

        domain_guidance = self._domain_artifact_guidance(specification)
        prompt = f"""
You are repairing a previously rejected implementation plan for one software task.
This is a FOCUSED FILE REPAIR, not a new whole-project plan.

ORIGINAL TASK:
{task.description}

TASK ID:
{task.task_id}

WORKSPACE:
{task.workspace}

ORIGINAL CONSTRAINTS:
{json.dumps(task.constraints, ensure_ascii=False)}

ORIGINAL ACCEPTANCE CRITERIA:
{json.dumps(task.acceptance_criteria, ensure_ascii=False)}

CURRENT WORKSPACE CONTEXT AND FILE INVENTORY:
{context if context.strip() else "(No additional workspace context.)"}

VALIDATION ERRORS THAT MUST BE FIXED:
{json.dumps(list(issues), ensure_ascii=False, indent=2)}

ONLY THESE FILES MAY BE RETURNED:
{chr(10).join(file_sections)}

FOCUSED REPAIR RULES:
- Return exactly one FileChange for every TARGET FILE listed above.
- Return no other path, no duplicate path, and do not omit any listed target.
- Do not create a new file to hold code. If a listed target needs a class or function, define it inside the listed file that owns that responsibility; a path outside TARGET FILE is rejected.
- In Python test files, tag each test with @pytest.mark.req_NNN for every requirement it verifies (REQ-002 becomes req_002), and start the file with `import pytest`.
- Keep every target path exactly as written, including directory and filename.
- Satisfy every acceptance criterion for every unit grouped under that target path.
- When a target has multiple units, combine all their obligations in that one file.
- Use create or modify according to the current workspace inventory and prior plan. Never delete a required artifact.
- Include complete, usable file content, not placeholders or a summary.
- Do not change unrelated files or weaken any requirement.
- Do not claim commands or tests were executed.
- Preserve prior valid details while fixing the listed validation errors.
- Return JSON only.

DOMAIN-SPECIFIC ARTIFACT GUIDANCE:
{domain_guidance if domain_guidance else "(No specialized artifact template applies.)"}

RETURN THIS JSON SHAPE:
{{
  "summary": "Focused repair of the listed ESpec artifacts",
  "changes": [
    {{
      "path": "exact/target/path",
      "change_type": "create",
      "reason": "Which failed ESpec obligations this file implements",
      "content": "Complete file content"
    }}
  ]
}}
"""
        try:
            response = self.client.generate_json(prompt)
            raw_changes = response.get("changes")
            if not isinstance(raw_changes, list):
                raise ValueError("changes must be a list")
            repaired: list[FileChange] = []
            seen: set[str] = set()
            dropped: list[str] = []
            for item in raw_changes:
                if not isinstance(item, dict):
                    raise ValueError("each change must be an object")
                path = str(item["path"]).replace("\\", "/")
                key = self._normalized_plan_path(path)
                if ignore_extra_paths and key not in requested_keys:
                    # Not requested in this call; the plan's existing version stays.
                    dropped.append(path)
                    continue
                if key in seen:
                    raise ValueError(f"duplicate repaired path: {path}")
                seen.add(key)
                change_type = ChangeType(item["change_type"])
                if change_type == ChangeType.DELETE:
                    raise ValueError(f"required ESpec artifact cannot be deleted: {path}")
                content = _normalize_model_file_content(path, item.get("content"))
                if not isinstance(content, str) or not content.strip():
                    raise ValueError(f"repaired file content must not be empty: {path}")
                issue_text = " ".join(issues).casefold()
                normalized_path = self._normalized_plan_path(path)
                if (
                    normalized_path in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
                    and "pg_isready" in issue_text
                ):
                    try:
                        content = ensure_postgres_pg_isready_healthcheck(content)
                    except CheckFailure as exc:
                        raise PlanningError(
                            f"Focused Compose repair could not enforce the required PostgreSQL healthcheck: {exc}"
                        ) from exc
                repaired.append(
                    FileChange(
                        path=path,
                        change_type=change_type,
                        reason=str(item["reason"]),
                        content=content,
                    )
                )
            if dropped:
                LOGGER.warning(
                    "Focused repair for %s also returned unrequested file(s) %s; they were ignored.",
                    ", ".join(requested), ", ".join(dropped),
                )
            if seen != requested_keys:
                missing = sorted(requested_keys - seen)
                extra = sorted(seen - requested_keys)
                details = []
                if missing:
                    details.append("missing: " + ", ".join(missing))
                if extra:
                    details.append("unexpected: " + ", ".join(extra))
                raise ValueError(
                    "focused repair must return exactly the requested paths ("
                    + "; ".join(details)
                    + ")"
                )
            return tuple(repaired)
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanningError(f"Could not validate focused ESpec file repair: {exc}") from exc

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
        for path, units in sorted(implementation_file_groups.items()):
            lines = [f"- FILE: {path}"]
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
- Treat the Engineering Specification's implementation_units array as the authoritative list of work that this plan must implement.
- Every implementation unit is mandatory.
- The plan format permits only one FileChange per path. Group implementation units by target_path and create exactly one planned file change for each unique target_path.
- When multiple units share the same target_path, that single file change must implement the combined acceptance criteria and contract obligations of ALL units in that path group. One change DOES cover all units in the same-path group when its content satisfies their combined criteria.
- Units with different target_path values are separate obligations and each requires its own planned file change at that exact path.
- Never create duplicate changes for the same path.
- Functional and non-functional requirements must be covered by all implementation units mapped to them; same-path units are implemented together as one file-change group.
IMPLEMENTATION FILE GROUPS (one planned change per listed path):
{implementation_file_groups_text}

ARTIFACT CONTENT CONTRACT:
- The file groups above repeat the exact acceptance obligations for each artifact. Treat every REQUIRED RESULT and UNIT ACCEPTANCE line as a mandatory content requirement.
- Do not return empty, skeletal, placeholder-only, or metadata-only content when the acceptance criteria require substantive configuration or documentation.
- For configuration files, include the complete required sections, values, references, and cross-file consistency. For project metadata, enumerate each required item rather than merely asserting that it exists.
- Before returning JSON, mentally audit every file's complete content against every acceptance line for that file. A path being present in changes does not mean its requirement is implemented.
- Preserve correct content when correcting an operation type; do not simplify or remove valid content.
- A constraint with no implementation-unit mapping is a compliance boundary, not a request to create or modify a file merely to demonstrate compliance. Respect it in every relevant change and follow its verification criteria.
- If a constraint is explicitly mapped to implementation units, those units are mandatory and must be implemented.
- A prohibition ("must not import X", "without Y") governs every file you write that it applies to. Before returning JSON, scan each such file's import lines and confirm none of the forbidden modules appear. Put any dependency the prohibited module would provide in a file the prohibition does not cover.
- Tests must check observable behavior with concrete expected values that follow from the requirement. Never write a placeholder test such as asserting that a function returned a truthy value.
- Behavior of a runnable program (its output, exit status, and error messages) must be tested by running the program in a test, for example with subprocess.run([sys.executable, "main.py", "2", "+", "3"], capture_output=True, text=True, cwd=<project root>) and asserting on stdout and returncode. Import sys and subprocess in that test file.
- For Python tests, tag each test function with a pytest marker for every requirement it verifies. The marker is the requirement ID in lowercase with non-alphanumeric characters replaced by "_" (REQ-002 becomes @pytest.mark.req_002). The verifier runs `python -m pytest -m req_002` for each requirement, so a requirement with no tagged test fails verification. Every test file that uses pytest.mark must start with `import pytest`.
- A test may call only methods and functions that your planned code defines. Before returning JSON, list every method a test calls on a planned class and confirm each one is defined in the planned files. Remove or rename any call you did not define.
- Only contracts referenced by implementation units are implementation obligations. Unreferenced contracts are context/verification descriptions and must not create extra file changes.
- Do not invent additional files or units to make coverage appear complete. If the ESpec has an inconsistency, explain it in risks rather than silently changing its scope.

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
- Only propose workspace-relative file paths. Never use "." or a directory-only path as a FileChange path.
- change_type must be create, modify, or delete.
- Treat WORKSPACE FILE INVENTORY as the authoritative list of currently existing eligible workspace files.
- If a proposed path appears in WORKSPACE FILE INVENTORY, use modify (or delete when removal is explicitly required); never use create for an existing path.
- If a proposed path is absent from WORKSPACE FILE INVENTORY, use create only when it is genuinely a new file. Inventory may be bounded; if marked truncated, do not assume an absent path is missing. Request/inspect additional context rather than guessing.
- Existing source files omitted from SOURCE FILES still exist; omission means their contents were not selected, not that they are absent.
- Prefer modifying existing files over creating duplicate alternatives.
- Create and modify operations require complete file content.
- Delete operations must use content null.
- Do not propose the same path more than once.
- Include complete content for every created or modified file.
- Prefer simple, maintainable solutions.
- Do not claim tests passed; you are only proposing a plan.
- Verification commands are suggestions only; deterministic checks are added by the orchestrator.
- Verification commands must run with programs that come with the project's language toolchain (for Python: python, python -m pytest, python -m unittest, python -c). Do not propose a linter or other tool unless the task or constraints name it, because a command that cannot be launched fails verification.
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
                raise ValueError(
                    "changes must be a non-empty list "
                    f"(model returned changes={raw_changes!r}; "
                    f"response keys={sorted(response.keys())}; "
                    f"summary={summary[:200]!r})"
                )

            changes = tuple(
                FileChange(
                    path=item["path"],
                    change_type=ChangeType(item["change_type"]),
                    reason=item["reason"],
                    content=_normalize_model_file_content(
                        item["path"], item.get("content")
                    ),
                )
                for item in raw_changes
            )

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
