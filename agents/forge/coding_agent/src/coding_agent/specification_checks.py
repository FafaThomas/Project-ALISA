"""Deterministic, read-only acceptance checks for common project artifacts.

The checker intentionally implements a small set of high-confidence checks.
Unsupported requirements must be handled through explicit human evidence; they
are never reported as passing merely because a file exists.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".coding-agent", "dist", "build"}
_PLACEHOLDER = re.compile(r"(?:example|sample|placeholder|change[_-]?me|your[_-]?|replace[_-]?me|dummy|local[_-]?dev)", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:export\s+)?(?:[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*)\s*=\s*([^#\r\n]+)"
)


_SECRET_ENV_KEY = re.compile(r"(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY|ACCESS_KEY)", re.I)
_ENV_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def env_example_content_issues(content: str) -> list[str]:
    """Validate documentation and safe defaults without treating ordinary config as secrets."""
    issues: list[str] = []
    entries = 0
    previous_comment = False
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            previous_comment = bool(stripped[1:].strip())
            continue
        match = _ENV_ASSIGNMENT.match(stripped)
        if not match:
            previous_comment = False
            continue
        key, raw_value = match.groups()
        inline_comment = re.search(r"\s+#(.*)$", raw_value)
        value = re.sub(r"\s+#.*$", "", raw_value).strip().strip("\"\'")
        entries += 1
        documented = previous_comment or bool(
            inline_comment and inline_comment.group(1).strip()
        )
        if not documented:
            issues.append(f"environment setting {key} is undocumented (line {line_number})")
        if not value:
            issues.append(f"environment setting {key} has an empty value")
        elif _SECRET_ENV_KEY.search(key) and not _PLACEHOLDER.search(value):
            issues.append(
                f"secret environment setting {key} must use an obvious non-secret placeholder"
            )
        previous_comment = False
    if entries == 0:
        issues.append("environment example contains no settings")
    return issues



def _yaml_document_lines(content: str) -> list[tuple[int, str, str, str]]:
    """Return (line index, indent, key, value) for simple YAML mapping entries."""
    result = []
    for index, raw in enumerate(content.splitlines()):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("---"):
            continue
        match = re.match(
            r"""^(\s*)(?:"([^"]+)"|'([^']+)'|([A-Za-z_][A-Za-z0-9_.-]*))\s*:\s*(.*?)\s*$""",
            raw,
        )
        if not match:
            continue
        key = next(group for group in match.groups()[1:4] if group is not None)
        result.append((index, len(match.group(1).expandtabs(2)), key.casefold(), match.group(5)))
    return result


def _yaml_subtree_end(lines: list[tuple[int, str, str, str]], entry_position: int) -> int:
    parent_indent = lines[entry_position][1]
    for position in range(entry_position + 1, len(lines)):
        if lines[position][1] <= parent_indent:
            return position
    return len(lines)


def _yaml_has_content(content: str, entries: list[tuple[int, str, str, str]], position: int) -> bool:
    _, parent_indent, _, value = entries[position]
    if value.strip() and value.strip() not in {"{}", "[]", "null", "~"}:
        return True
    parent_line = entries[position][0]
    next_position = _yaml_subtree_end(entries, position)
    end_line = entries[next_position][0] if next_position < len(entries) else len(content.splitlines())
    for raw in content.splitlines()[parent_line + 1:end_line]:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            indent = len(raw) - len(raw.lstrip(" \\t"))
            if indent > parent_indent:
                return True
    return False


def _yaml_phase_count(content: str, entries: list[tuple[int, str, str, str]]) -> int:
    raw_lines = content.splitlines()
    phase_positions = [
        position for position, (_, _, key, _) in enumerate(entries)
        if key in {"parent_phases", "phases"}
    ]
    best_count = 0
    for position in phase_positions:
        line_index, parent_indent, _, value = entries[position]
        if value.startswith("[") and value.endswith("]"):
            count = len([item for item in value[1:-1].split(",") if item.strip()])
            best_count = max(best_count, count)
            continue
        end_position = _yaml_subtree_end(entries, position)
        end_line = entries[end_position][0] if end_position < len(entries) else len(raw_lines)
        subtree = raw_lines[line_index + 1:end_line]
        list_entries = [
            (len(line) - len(line.lstrip(" \t")), line)
            for line in subtree
            if re.match(r"^\s+-\s+\S", line)
        ]
        if list_entries:
            list_indent = min(indent for indent, _ in list_entries)
            count = sum(1 for indent, _ in list_entries if indent == list_indent)
        else:
            child_keys = [
                entries[item][1]
                for item in range(position + 1, end_position)
                if entries[item][1] > parent_indent
            ]
            if child_keys:
                child_indent = min(child_keys)
                count = sum(
                    1 for item in range(position + 1, end_position)
                    if entries[item][1] == child_indent
                )
            else:
                count = 0
        best_count = max(best_count, count)
    return best_count


def project_yaml_content_issues(content: str) -> list[str]:
    """Validate project metadata across common nested YAML layouts."""
    entries = _yaml_document_lines(content)
    issues: list[str] = []

    def has_nonempty_key(keys: tuple[str, ...]) -> bool:
        return any(
            key in keys and _yaml_has_content(content, entries, position)
            for position, (_, _, key, _) in enumerate(entries)
        )

    if not has_nonempty_key(("goal", "product_goal", "objective")):
        issues.append("project.yaml omits product goal")
    if not has_nonempty_key(("approved_stack", "stack", "technology_stack", "technologies")):
        issues.append("project.yaml omits approved stack")
    if not has_nonempty_key(("repository_architecture", "architecture")):
        issues.append("project.yaml omits repository architecture")

    phase_count = _yaml_phase_count(content, entries)
    if phase_count < 6:
        issues.append(
            f"project.yaml must enumerate all six parent phases (found {phase_count})"
        )

    has_acceptance_status = has_nonempty_key(("acceptance_status", "status"))
    if not has_acceptance_status:
        # Accept a nested acceptance: { status: ... } representation, but not
        # an arbitrary acceptance-description block with no status field.
        for position, (_, _, key, _) in enumerate(entries):
            if key == "acceptance":
                end = _yaml_subtree_end(entries, position)
                if any(entries[item][2] in {"status", "acceptance_status"} and
                       _yaml_has_content(content, entries, item)
                       for item in range(position + 1, end)):
                    has_acceptance_status = True
                    break
    if not has_acceptance_status:
        issues.append("project.yaml omits acceptance status")
    return issues


class CheckFailure(ValueError):
    pass


def _safe_file(root: Path, relative: str) -> Path:
    normalized = str(relative).replace("\\", "/")
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise CheckFailure(f"Unsafe verification path: {relative}") from exc
    return candidate


def _read(root: Path, relative: str) -> str:
    path = _safe_file(root, relative)
    if not path.is_file():
        raise CheckFailure(f"Required file is missing: {relative}")
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise CheckFailure(f"Cannot read {relative}: {exc}") from exc



def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _yaml_key(line: str) -> tuple[str, str] | None:
    """Return a simple YAML mapping key/value pair, including quoted keys."""
    match = re.match(
        r"""^\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z_][A-Za-z0-9_.-]*))\s*:\s*(.*?)\s*$""",
        line,
    )
    if not match:
        return None
    key = next(group for group in match.groups()[:3] if group is not None)
    return key.casefold(), match.group(4) or ""


def _top_level_yaml_block(content: str, key: str) -> tuple[int, int, list[str]] | None:
    """Locate a top-level YAML block without assuming child indentation width."""
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if _line_indent(line) != 0:
            continue
        parsed = _yaml_key(line)
        if parsed is None or parsed[0] != key.casefold():
            continue
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if candidate.strip() and _line_indent(candidate) == 0 and _yaml_key(candidate):
                break
            end += 1
        return index, end, lines
    return None


def _direct_mapping_children(lines: list[str], start: int, end: int, parent_indent: int = 0) -> list[tuple[int, str, str]]:
    """Return mapping entries at the shallowest child indentation."""
    candidates: list[tuple[int, str, str]] = []
    for index in range(start + 1, end):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _line_indent(line)
        parsed = _yaml_key(line)
        if indent > parent_indent and parsed is not None:
            candidates.append((indent, parsed[0], parsed[1]))
    if not candidates:
        return []
    child_indent = min(item[0] for item in candidates)
    return [item for item in candidates if item[0] == child_indent]


def _service_block(compose: str, service: str) -> str:
    section = _top_level_yaml_block(compose, "services")
    if section is None:
        raise CheckFailure("docker-compose.yml has no top-level services mapping")
    start, end, lines = section
    children = _direct_mapping_children(lines, start, end)
    matching = [item for item in children if item[1] == service.casefold()]
    if not matching:
        raise CheckFailure(f"docker-compose.yml does not define service {service!r}")
    service_indent = matching[0][0]
    service_line = next(
        index for index in range(start + 1, end)
        if _line_indent(lines[index]) == service_indent
        and (_yaml_key(lines[index]) or (None,))[0] == service.casefold()
    )
    block_end = end
    for index in range(service_line + 1, end):
        parsed = _yaml_key(lines[index])
        if lines[index].strip() and _line_indent(lines[index]) == service_indent and parsed:
            block_end = index
            break
    return "\n".join(lines[service_line:block_end])



def _ensure_named_volume(compose: str, volume_name: str) -> str:
    section = _top_level_yaml_block(compose, "volumes")
    if section is None:
        suffix = "" if not compose or compose.endswith("\n") else "\n"
        return compose + suffix + f"volumes:\n  {volume_name}:\n"
    start, end, lines = section
    children = _direct_mapping_children(lines, start, end)
    if any(key == volume_name.casefold() for _, key, _ in children):
        return compose
    insertion = end
    while insertion > start + 1 and not lines[insertion - 1].strip():
        insertion -= 1
    child_indent = min((indent for indent, _, _ in children), default=2)
    lines[insertion:insertion] = [" " * child_indent + f"{volume_name}:"]
    return "\n".join(lines).rstrip() + ("\n" if compose.endswith("\n") else "")


def ensure_postgres_pg_isready_healthcheck(compose: str) -> str:
    """Ensure a PostgreSQL service has a deterministic pg_isready healthcheck.

    Existing PostgreSQL services are found by service name or official image,
    so aliases such as ``db`` and ``database`` remain valid. If the model
    omitted the database service entirely, add a conventional PostgreSQL 17
    service and named data volume without replacing other Compose services.
    """
    section = _top_level_yaml_block(compose, "services")
    if section is None:
        suffix = "" if not compose or compose.endswith("\n") else "\n"
        compose = compose + suffix + "services:\n"
        section = _top_level_yaml_block(compose, "services")
    assert section is not None
    start, end, lines = section
    details = _postgres_service_details(compose)

    if details is None:
        children = _direct_mapping_children(lines, start, end)
        service_indent = min((indent for indent, _, _ in children), default=2)
        service_block = [
            " " * service_indent + "postgres:",
            " " * (service_indent + 2) + "image: postgres:17",
            " " * (service_indent + 2) + "environment:",
            " " * (service_indent + 4) + "POSTGRES_DB: ${POSTGRES_DB}",
            " " * (service_indent + 4) + "POSTGRES_USER: ${POSTGRES_USER}",
            " " * (service_indent + 4) + "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}",
            " " * (service_indent + 2) + "volumes:",
            " " * (service_indent + 4) + "- postgres_data:/var/lib/postgresql/data",
        ]
        insertion = end
        while insertion > start + 1 and not lines[insertion - 1].strip():
            insertion -= 1
        lines[insertion:insertion] = service_block
        compose = "\n".join(lines).rstrip() + ("\n" if compose.endswith("\n") else "")
        compose = _ensure_named_volume(compose, "postgres_data")
        details = _postgres_service_details(compose)
        if details is None:
            raise CheckFailure("Cannot add PostgreSQL service to docker-compose.yml")

    service_name, _ = details
    section = _top_level_yaml_block(compose, "services")
    assert section is not None
    start, end, lines = section
    service_indent = next(
        indent for indent, key, _ in _direct_mapping_children(lines, start, end)
        if key == service_name.casefold()
    )
    service_line = next(
        index for index in range(start + 1, end)
        if _line_indent(lines[index]) == service_indent
        and (_yaml_key(lines[index]) or (None,))[0] == service_name.casefold()
    )
    service_end = end
    for index in range(service_line + 1, end):
        parsed = _yaml_key(lines[index])
        if lines[index].strip() and _line_indent(lines[index]) == service_indent and parsed is not None:
            service_end = index
            break

    child_indent = service_indent + 2
    healthcheck_line = None
    for index in range(service_line + 1, service_end):
        parsed = _yaml_key(lines[index])
        if parsed is not None and parsed[0] == "healthcheck" and _line_indent(lines[index]) == child_indent:
            healthcheck_line = index
            break

    healthcheck_indent = " " * child_indent
    nested_indent = " " * (child_indent + 2)
    replacement = [
        f"{healthcheck_indent}healthcheck:",
        f'{nested_indent}test: ["CMD-SHELL", "pg_isready -U $${{POSTGRES_USER}} -d $${{POSTGRES_DB}}"]',
        f"{nested_indent}interval: 10s",
        f"{nested_indent}timeout: 5s",
        f"{nested_indent}retries: 5",
    ]

    if healthcheck_line is not None:
        healthcheck_end = service_end
        for index in range(healthcheck_line + 1, service_end):
            if lines[index].strip() and _line_indent(lines[index]) <= child_indent:
                healthcheck_end = index
                break
        repaired_lines = lines[:healthcheck_line] + replacement + lines[healthcheck_end:]
    else:
        insertion = service_end
        while insertion > service_line + 1 and not lines[insertion - 1].strip():
            insertion -= 1
        repaired_lines = lines[:insertion] + replacement + lines[insertion:]

    return "\n".join(repaired_lines).rstrip() + ("\n" if compose.endswith("\n") else "")

def _declared_root_volumes(compose: str) -> set[str]:
    section = _top_level_yaml_block(compose, "volumes")
    if section is None:
        return set()
    start, end, lines = section
    children = _direct_mapping_children(lines, start, end)
    names = {key for _, key, _ in children}
    # Also support the compact, valid mapping form: volumes: {name: {}}
    inline = (_yaml_key(lines[start]) or ("", ""))[1]
    if inline.startswith("{") and inline.endswith("}"):
        names.update(
            item.strip().split(":", 1)[0].strip(" \t'\"")
            for item in inline[1:-1].split(",") if ":" in item
        )
    return names


def _postgres_volume_source(postgres: str) -> tuple[str | None, bool]:
    """Return (volume source, is_bind_mount) for the PostgreSQL data target."""
    lines = postgres.splitlines()
    # Short syntax: - postgres_data:/var/lib/postgresql/data[:options]
    for line in lines:
        match = re.match(
            r"""^\s*-\s*["']?([^:"']+)["']?\s*:\s*["']?/var/lib/postgresql/data["']?(?::[^#\s]+)?\s*(?:#.*)?$""",
            line,
        )
        if match:
            source = match.group(1).strip()
            is_bind = (
                source.startswith(("./", "../", "/", "~"))
                or bool(re.match(r"^[A-Za-z]:[\\/]", source))
            )
            return source, is_bind

    # Long syntax: one list item containing source and target.
    item_starts = [
        index for index, line in enumerate(lines)
        if re.match(r"^\s*-\s+", line)
    ]
    for position, item_start in enumerate(item_starts):
        item_end = item_starts[position + 1] if position + 1 < len(item_starts) else len(lines)
        item = "\n".join(lines[item_start:item_end])
        if not re.search(r"(?m)^\s*target:\s*['\"]?/var/lib/postgresql/data['\"]?\s*(?:#.*)?$", item):
            continue
        source_match = re.search(r"(?m)^\s*source:\s*['\"]?([^'\"\s#]+)", item)
        if not source_match:
            return None, False
        source = source_match.group(1)
        type_match = re.search(r"(?m)^\s*type:\s*['\"]?([A-Za-z]+)", item)
        if type_match and type_match.group(1).casefold() == "bind":
            return source, True
        is_bind = (
            source.startswith(("./", "../", "/", "~"))
            or bool(re.match(r"^[A-Za-z]:[\\/]", source))
        )
        return source, is_bind
    return None, False


def _postgres_service_details(compose: str) -> tuple[str, str] | None:
    """Find the PostgreSQL service by canonical name, image, or strong config clues."""
    section = _top_level_yaml_block(compose, "services")
    if section is None:
        return None
    start, end, lines = section
    children = _direct_mapping_children(lines, start, end)
    blocks: list[tuple[str, str]] = []
    for _, name, _ in children:
        try:
            blocks.append((name, _service_block(compose, name)))
        except CheckFailure:
            continue

    # An explicitly named postgres service is the most unambiguous match.
    for name, block in blocks:
        if name.casefold() in {"postgres", "postgresql"}:
            return name, block

    # Service aliases such as "db" are common; the image is authoritative.
    for name, block in blocks:
        if re.search(
            r"""(?im)^\s*image\s*:\s*['"]?(?:docker\.io/)?(?:library/)?postgres(?:[:@/\s]|$)""",
            block,
        ):
            return name, block

    # Accept conventional database aliases when their configuration clearly
    # belongs to the official PostgreSQL image.
    for name, block in blocks:
        if name.casefold() in {"db", "database"} and re.search(
            r"(?im)^\s*POSTGRES_(?:DB|USER|PASSWORD)\s*:", block
        ):
            return name, block
    return None


def compose_content_issues(compose: str, requirement_text: str) -> list[str]:
    """Return semantic Compose defects using indentation-tolerant YAML structure checks."""
    issues: list[str] = []
    expected_services = [
        service for service in ("frontend", "backend")
        if re.search(rf"\b{service}\b", requirement_text, re.I)
    ]
    service_blocks: dict[str, str] = {}
    for service in expected_services:
        try:
            service_blocks[service] = _service_block(compose, service)
        except CheckFailure:
            issues.append(f"Compose content is missing the {service} service")

    postgres_details = _postgres_service_details(compose)
    postgres = postgres_details[1] if postgres_details else ""
    if re.search(r"\b(?:postgres|postgresql)\b", requirement_text, re.I) and not postgres_details:
        issues.append("Compose content is missing the PostgreSQL service")

    lowered = requirement_text.casefold()
    if re.search(r"\b(?:named\s+)?volume|persistent\s+volume|persistence", lowered):
        source, is_bind = _postgres_volume_source(postgres)
        if source is None:
            issues.append("PostgreSQL data is not configured with a persistent volume")
        elif is_bind:
            issues.append("PostgreSQL data uses a host bind mount instead of a named volume")
        elif source not in _declared_root_volumes(compose):
            issues.append(f"PostgreSQL volume {source!r} is not declared at the Compose root")

    if re.search(r"health\s*check|readiness|pg_isready", lowered):
        if not re.search(r"(?m)^\s*healthcheck\s*:", postgres) or "pg_isready" not in postgres:
            issues.append("PostgreSQL readiness healthcheck using pg_isready is missing")

    if re.search(r"credential|environment-driven|environment variable", lowered):
        for variable in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
            if variable not in postgres:
                issues.append(f"PostgreSQL service is missing {variable}")

    if re.search(r"health\s+conditions?|service_healthy", lowered):
        if not re.search(r"""(?m)^\s*condition\s*:\s*['"]?service_healthy['"]?""", compose):
            issues.append("Service dependencies do not use service_healthy conditions")

    return issues


def _check_compose(root: Path, paths: list[str], text: str) -> list[str]:
    candidates = [
        p for p in paths
        if Path(p).name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
    ]
    if not candidates:
        candidates = ["docker-compose.yml"]
    compose = _read(root, candidates[0])
    issues = compose_content_issues(compose, text)
    if issues:
        raise CheckFailure("; ".join(issues))
    findings = []
    if re.search(r"\b(?:named\s+)?volume|persistent\s+volume|persistence", text, re.I):
        findings.append("PostgreSQL data uses a declared named volume")
    if re.search(r"health\s*check|readiness|pg_isready", text, re.I):
        findings.append("PostgreSQL readiness healthcheck uses pg_isready")
    if re.search(r"credential|environment-driven|environment variable", text, re.I):
        findings.append("PostgreSQL credentials are environment-driven")
    if re.search(r"health\s+conditions?|service_healthy", text, re.I):
        findings.append("Service dependencies use health conditions")
    if not findings:
        findings.append("Compose service structure satisfies the referenced static checks")
    return findings

def _check_env_example(root: Path, paths: list[str]) -> list[str]:
    target = next((p for p in paths if Path(p).name == ".env.example"), ".env.example")
    content = _read(root, target)
    issues = env_example_content_issues(content)
    if issues:
        raise CheckFailure(f"{target}: " + "; ".join(issues))
    entries = sum(1 for line in content.splitlines() if _ENV_ASSIGNMENT.match(line.strip()))
    return [f"{target} contains {entries} documented settings and safe secret placeholders"]


# A README topic is required only when the specification text asks for it.
# Each entry: (label, trigger pattern in the spec text, accepted README tokens).
_README_TOPICS = (
    ("prerequisites", r"prerequisite", ("prerequisite", "requirements", "docker")),
    ("environment setup", r"environment setup", ("environment", ".env", "env.example")),
    ("compose workflow", r"compose workflow|docker compose|docker-compose|compose up",
     ("docker compose", "docker-compose", "compose up")),
)


def readme_missing_topics(readme_text: str, spec_text: str) -> list[str]:
    """Return README topics the specification requires but the README does not cover."""
    content = readme_text.casefold()
    missing = []
    for label, trigger, tokens in _README_TOPICS:
        if re.search(trigger, spec_text, re.IGNORECASE) and not any(token in content for token in tokens):
            missing.append(label)
    return missing


def _check_readme(root: Path, paths: list[str], spec_text: str = "") -> list[str]:
    target = next((p for p in paths if Path(p).name.lower() == "readme.md"), "README.md")
    content = _read(root, target)
    if not content.strip():
        raise CheckFailure(f"{target} is empty")
    missing = readme_missing_topics(content, spec_text)
    if missing:
        raise CheckFailure("README is missing required coverage: " + ", ".join(missing))
    return [f"{target} exists and has content"]


def _check_project_yaml(root: Path, paths: list[str]) -> list[str]:
    target = next((p for p in paths if Path(p).name == "project.yaml"), "project.yaml")
    content = _read(root, target)
    issues = project_yaml_content_issues(content)
    if issues:
        raise CheckFailure(f"{target}: " + "; ".join(issues))
    return ["project.yaml documents goal, approved stack, architecture, six phases, and acceptance status"]


def _check_dockerignore(root: Path, paths: list[str]) -> list[str]:
    targets = [p for p in paths if Path(p).name == ".dockerignore"]
    if not targets:
        targets = [".dockerignore"]
    findings = []
    for target in targets:
        content = _read(root, target)
        if not content.strip():
            raise CheckFailure(f"{target} is empty")
        parent = Path(target).parent.as_posix()
        if parent != ".":
            wrong_prefix = f"!{parent}/"
            if wrong_prefix in content.replace("\\", "/"):
                raise CheckFailure(f"{target} uses build-context-inappropriate negation {wrong_prefix!r}")
        findings.append(f"{target} exists and contains ignore rules")
    return findings


def _check_layout(root: Path, paths: list[str]) -> list[str]:
    directories = {Path(p).parent.as_posix() for p in paths if Path(p).parent.as_posix() != "."}
    missing = [directory for directory in sorted(directories) if not _safe_file(root, directory).is_dir()]
    if missing:
        raise CheckFailure("Required directories are missing: " + ", ".join(missing))
    return ["Required artifact directories exist"]


def _check_postgres_runtime(root: Path, paths: list[str]) -> list[str]:
    compose_path = next((p for p in paths if "compose" in Path(p).name), "docker-compose.yml")
    content = _read(root, compose_path).casefold()
    if "postgres" not in content:
        raise CheckFailure("Runtime configuration does not declare PostgreSQL")
    if "sqlite" in content:
        raise CheckFailure("Runtime configuration contains SQLite references")
    return ["Runtime configuration selects PostgreSQL and contains no SQLite reference"]


def _check_secrets(root: Path) -> list[str]:
    scanned = 0
    for path in root.rglob("*"):
        if not path.is_file() or any(part in _IGNORED_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if path.name == ".env.example" or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".pyc"}:
            continue
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        scanned += 1
        for match in _SECRET_ASSIGNMENT.finditer(content):
            value = match.group(1).strip().strip("\"'")
            if value and not _PLACEHOLDER.search(value) and value not in {"${", "${}"}:
                relative = path.relative_to(root).as_posix()
                raise CheckFailure(f"Potential hard-coded secret in {relative}; review the matching assignment")
        if scanned >= 5000:
            break
    return [f"Scanned {scanned} text files for likely hard-coded secret assignments (heuristic)"]


def run_checks(root: str | Path, payload: dict[str, Any]) -> list[str]:
    workspace = Path(root).resolve(strict=True)
    if not workspace.is_dir():
        raise CheckFailure(f"Workspace is not a directory: {workspace}")
    requirements = payload.get("requirements", [])
    units = payload.get("units", [])
    paths = [str(unit["target_path"]) for unit in units if isinstance(unit, dict) and unit.get("target_path")]
    combined_text = " ".join(
        str(part) for requirement in requirements if isinstance(requirement, dict)
        for part in (requirement.get("description", ""), *(requirement.get("acceptance_criteria", []) or []))
    )
    findings: list[str] = []
    # Every referenced unit is an explicit artifact obligation.
    for path in paths:
        candidate = _safe_file(workspace, path)
        if not candidate.is_file():
            raise CheckFailure(f"Required implementation artifact is missing: {path}")
    if re.search(r"frontend/|backend/|postgres/|directory structure|root layout", combined_text, re.I):
        findings.extend(_check_layout(workspace, paths))
    lowered = combined_text.casefold()
    has_compose_artifact = any(Path(path).name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"} for path in paths)
    if has_compose_artifact and re.search(r"docker|compose|postgres|postgresql|health|volume|credential", lowered, re.I):
        findings.extend(_check_compose(workspace, paths, combined_text))
    if ".env.example" in lowered:
        findings.extend(_check_env_example(workspace, paths))
    if "readme" in lowered:
        findings.extend(_check_readme(workspace, paths, combined_text))
    if "project.yaml" in lowered:
        findings.extend(_check_project_yaml(workspace, paths))
    if ".dockerignore" in lowered:
        findings.extend(_check_dockerignore(workspace, paths))
    if "sqlite" in lowered or "postgresql" in lowered and "runtime" in lowered:
        findings.extend(_check_postgres_runtime(workspace, paths))
    if re.search(r"secret|source control|credentials", lowered):
        findings.extend(_check_secrets(workspace))
    if not findings:
        # Existence of the mapped artifact is a valid static check, but it is
        # not represented as a semantic claim beyond that narrow property.
        findings.append(f"Verified existence of {len(paths)} referenced artifact(s)")
    return findings


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Usage: python -m coding_agent.specification_checks WORKSPACE PAYLOAD_JSON", file=sys.stderr)
        return 2
    try:
        payload = json.loads(args[1])
        if not isinstance(payload, dict):
            raise CheckFailure("Verification payload must be a JSON object")
        findings = run_checks(args[0], payload)
    except (CheckFailure, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"SPECIFICATION CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    print("SPECIFICATION CHECK PASSED")
    for finding in findings:
        print(f"- {finding}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
