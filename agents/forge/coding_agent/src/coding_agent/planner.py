
"""Turn coding tasks into structured implementation plans using an LLM."""

from __future__ import annotations

import json

from coding_agent.llm_client import OllamaClient
from coding_agent.models import (
    ChangeType,
    CodingTask,
    FileChange,
    ImplementationPlan,
)


class PlanningError(RuntimeError):
    """Raised when the LLM cannot produce a valid implementation plan."""


class CodingPlanner:
    """Generate validated implementation plans without modifying files."""

    def __init__(self, client: OllamaClient | None = None) -> None:
        self.client = client or OllamaClient()

    def plan(
        self,
        task: CodingTask,
        context: str = "",
    ) -> ImplementationPlan:
        """Ask the LLM to propose changes for a coding task."""

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

EXISTING CODE CONTEXT:
{context if context.strip() else "(No existing code provided.)"}

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
  "verification_commands": [
    ["python", "-m", "pytest", "-q"]
  ],
  "requires_approval": true,
  "risks": []
}}

RULES:
- Only propose workspace-relative paths.
- change_type must be create, modify, or delete.
- Create and modify operations require complete file content.
- Delete operations must use content null.
- Do not propose the same path more than once.
- Include complete content for every created or modified file.
- Prefer simple, maintainable solutions.
- Do not claim tests passed; you are only proposing a plan.
- Verification commands are suggestions and must not be executed by you.
- Do not modify unrelated files.
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

            changes = tuple(
                FileChange(
                    path=item["path"],
                    change_type=ChangeType(item["change_type"]),
                    reason=item["reason"],
                    content=item.get("content"),
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
