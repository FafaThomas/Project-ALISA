"""Deterministic, narrowly scoped repairs for common plan artifacts.

These repairs only fill mechanical omissions that can be derived safely from
explicit ESpec implementation units. They do not invent application behavior
or bypass the normal plan/specification validator.
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import PurePosixPath

from .engineering_spec import EngineeringSpecification
from .models import ChangeType, FileChange, ImplementationPlan


_SECRET_KEY = re.compile(r"(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY|ACCESS_KEY)", re.I)
_PLACEHOLDER = re.compile(
    r"(?:example|sample|placeholder|change[_-]?me|your[_-]?|replace[_-]?me|dummy|local[_-]?dev)",
    re.I,
)
_ASSIGNMENT = re.compile(r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<sep>\s*=\s*)(?P<value>.*)$")


def _dockerignore_content(path: str) -> str:
    """Return conservative exclusions for a root or service Docker context."""
    parent = PurePosixPath(path).parent.as_posix()
    lines = [
        "# Local secrets and environment files",
        ".env",
        ".env.*",
        "!.env.example",
        "",
        "# Version control and editor files",
        ".git",
        ".vscode",
        ".DS_Store",
        "",
        "# Python caches and virtual environments",
        "__pycache__/",
        "*.py[cod]",
        ".venv/",
        "venv/",
        "",
        "# JavaScript dependencies",
        "node_modules/",
    ]
    if parent not in {"", "."}:
        # A service build context should not exclude itself by referring to
        # root-relative paths. The patterns above are relative to that context.
        lines.insert(0, f"# Build-context exclusions for {parent}")
    else:
        lines.insert(0, "# Root Docker build-context exclusions")
    return "\n".join(lines) + "\n"


def _setting_description(key: str) -> str:
    descriptions = {
        "POSTGRES_DB": "Database name used by PostgreSQL",
        "POSTGRES_USER": "Database user used by the application",
        "POSTGRES_PASSWORD": "Non-secret placeholder for the local database password",
        "POSTGRES_HOST": "PostgreSQL service hostname on the Compose network",
        "POSTGRES_PORT": "PostgreSQL service port",
        "DB_NAME": "Application database name",
        "DB_USER": "Application database username",
        "DB_PASSWORD": "Non-secret placeholder for the application database password",
        "DB_HOST": "Database service hostname",
        "DB_PORT": "Database service port",
        "FRONTEND_PORT": "Host port exposed by the frontend",
        "BACKEND_PORT": "Host port exposed by the backend",
        "FRONTEND_API_URL": "Backend API URL used by the frontend",
        "BACKEND_DEBUG": "Backend development debug setting",
        "FRONTEND_HOST": "Frontend service hostname",
        "BACKEND_HOST": "Backend service hostname",
    }
    return descriptions.get(key, f"Configuration setting {key} used by the application")


def normalize_env_example(content: str) -> tuple[str, tuple[str, ...]]:
    """Document every assignment and replace unsafe secret examples."""
    output: list[str] = []
    repairs: list[str] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        match = _ASSIGNMENT.match(line)
        if not match:
            output.append(line)
            continue

        key = match.group("key")
        raw_value = match.group("value")
        inline_comment = ""
        value_text = raw_value
        comment_match = re.search(r"\s+#.*$", raw_value)
        if comment_match:
            inline_comment = comment_match.group(0)
            value_text = raw_value[:comment_match.start()]
        semantic_value = value_text.strip().strip("\"'")
        if not semantic_value:
            value_text = "change_me" if _SECRET_KEY.search(key) else "example_value"
            repairs.append(f"provided a safe example value for {key}")
        elif _SECRET_KEY.search(key) and not _PLACEHOLDER.search(semantic_value):
            value_text = "change_me"
            repairs.append(f"replaced the non-placeholder secret example for {key}")
        else:
            value_text = value_text.rstrip()

        # Always add a key-specific explanation. A generic file-level comment
        # is not sufficient documentation for each setting.
        setting_comment = f"# {_setting_description(key)}."
        if not output or output[-1] != setting_comment:
            output.append(setting_comment)
        output.append(f"{match.group('prefix')}{key}{match.group('sep')}{value_text}{inline_comment}")
        repairs.append(f"documented {key}")

    normalized = "\n".join(output)
    if content.endswith(("\n", "\r")):
        normalized += "\n"
    return normalized, tuple(dict.fromkeys(repairs))



def _has_root_acceptance_status(content: str) -> bool:
    """Accept root or project-scoped status, not unrelated phase statuses."""
    lines = content.splitlines()
    parent_stack: list[tuple[int, str]] = []
    status_block_indent: int | None = None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" \\t"))
        match = re.match(r"^\\s*([A-Za-z_][A-Za-z0-9_-]*)\\s*:\\s*(.*?)\\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        value = re.sub(r"\\s+#.*$", "", value).strip()
        while parent_stack and parent_stack[-1][0] >= indent:
            parent_stack.pop()
        ancestors = {name for _, name in parent_stack}
        allowed_status_key = (
            key == "acceptance_status"
            and (indent == 0 or "project" in ancestors or "acceptance" in ancestors)
        ) or (
            key == "status"
            and (indent == 0 or "acceptance" in ancestors)
        )
        if allowed_status_key and value:
            return True
        if allowed_status_key and not value:
            status_block_indent = indent
        if not value:
            parent_stack.append((indent, key))
        if status_block_indent is not None and indent > status_block_indent and value:
            return True
        if indent <= (status_block_indent if status_block_indent is not None else -1):
            status_block_indent = None
    return False

def normalize_project_yaml_acceptance_status(content: str) -> tuple[str, bool]:
    """Add an honest pending root acceptance status when only phase statuses exist."""
    if _has_root_acceptance_status(content):
        return content, False
    normalized = content.rstrip()
    if normalized:
        normalized += "\n"
    normalized += "acceptance_status: pending\n"
    return normalized, True

def repair_plan_artifacts(
    plan: ImplementationPlan,
    specification: EngineeringSpecification,
) -> tuple[ImplementationPlan, tuple[str, ...]]:
    """Repair omitted .dockerignore units and mechanical config/metadata omissions.

    The normal ESpec plan validator still runs after this function. Unsupported
    artifact types and substantive configuration defects remain model/planner
    responsibilities and are never silently accepted here.
    """
    changes = list(plan.changes)
    by_path = {change.path.replace("\\", "/").rstrip("/"): index
               for index, change in enumerate(changes)}
    repairs: list[str] = []

    for unit in specification.implementation_units:
        target = unit.target_path.replace("\\", "/").rstrip("/")
        if PurePosixPath(target).name != ".dockerignore" or target in by_path:
            continue
        changes.append(FileChange(
            path=target,
            change_type=ChangeType.CREATE,
            reason="Deterministic repair: include the explicitly required Docker ignore file.",
            content=_dockerignore_content(target),
        ))
        by_path[target] = len(changes) - 1
        repairs.append(f"added omitted .dockerignore implementation unit at {target}")

    for unit in specification.implementation_units:
        target = unit.target_path.replace("\\", "/").rstrip("/")
        if PurePosixPath(target).name != ".env.example":
            continue
        index = by_path.get(target)
        if index is None:
            continue
        change = changes[index]
        if change.change_type not in {ChangeType.CREATE, ChangeType.MODIFY} or change.content is None:
            continue
        normalized, env_repairs = normalize_env_example(change.content)
        if normalized != change.content:
            changes[index] = replace(change, content=normalized)
            repairs.extend(f"{target}: {item}" for item in env_repairs)

    for unit in specification.implementation_units:
        target = unit.target_path.replace("\\", "/").rstrip("/")
        if PurePosixPath(target).name != "project.yaml":
            continue
        index = by_path.get(target)
        if index is None:
            continue
        change = changes[index]
        if change.change_type not in {ChangeType.CREATE, ChangeType.MODIFY} or change.content is None:
            continue
        normalized, added = normalize_project_yaml_acceptance_status(change.content)
        if added:
            changes[index] = replace(change, content=normalized)
            repairs.append(f"{target}: added pending root acceptance_status")

    if not repairs:
        return plan, ()
    return replace(plan, changes=tuple(changes)), tuple(repairs)
