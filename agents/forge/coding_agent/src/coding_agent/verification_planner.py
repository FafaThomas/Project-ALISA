"""Build deterministic verification commands from approved file changes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from coding_agent.models import FileChange
from coding_agent.specification_check_planner import command_rejection, project_toolchain


# This script checks only the paths changed by the approved plan. Python source
# is compiled in memory, so it does not create __pycache__ or .pyc artifacts.
_FILE_CHECK_SCRIPT = r'''import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
changes = json.loads(sys.argv[2])
errors = []
for item in changes:
    candidate = (root / item["path"]).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        errors.append(f"Path escapes workspace: {item['path']}")
        continue
    operation = item["operation"]
    if operation == "delete":
        if candidate.exists():
            errors.append(f"Deleted path still exists: {item['path']}")
        continue
    if not candidate.is_file():
        errors.append(f"Expected file is missing: {item['path']}")
        continue
    if candidate.suffix.lower() == ".py":
        try:
            source = candidate.read_text(encoding="utf-8-sig")
            compile(source, str(candidate), "exec")
        except Exception as exc:
            errors.append(f"Python syntax check failed for {item['path']}: {exc}")
if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
print(f"Verified {len(changes)} changed path(s); Python syntax checks completed in memory.")
'''


class VerificationPlanner:
    """Select baseline checks without trusting model-generated test commands."""

    _IGNORED_DIRS = {
        ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
        "node_modules", "dist", "build", ".coding-agent",
    }

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).resolve()

    def _has_pytest_tests(self, proposed_changes: Iterable[FileChange] = ()) -> bool:
        for change in proposed_changes:
            candidate = Path(change.path.replace("\\", "/"))
            if candidate.suffix.lower() == ".py" and (
                candidate.name.startswith("test_")
                or candidate.name.endswith("_test.py")
            ) and change.change_type.value != "delete":
                return True

        for path in self.root.rglob("*.py"):
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue
            if any(part in self._IGNORED_DIRS for part in relative.parts[:-1]):
                continue
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                return True
        return False

    @staticmethod
    def _is_pytest(command: tuple[str, ...]) -> bool:
        lowered = tuple(part.casefold() for part in command)
        return "pytest" in lowered or (
            len(lowered) >= 3 and lowered[:3] == ("python", "-m", "pytest")
        )

    def _node_project_commands(self) -> tuple[tuple[str, ...], ...]:
        """Select declared Node project checks from package.json.

        The manifest is read as data; no package script is inferred or invented.
        npm executes the declared script without shell=True in CommandRunner.
        """
        manifest = self.root / "package.json"
        if not manifest.is_file():
            return ()
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ()
        scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
        if not isinstance(scripts, dict):
            return ()
        return tuple(
            ("npm", "run", name)
            for name in ("build", "test", "lint")
            if isinstance(scripts.get(name), str) and scripts[name].strip()
        )

    def build(
        self,
        changes: Iterable[FileChange],
        suggested_commands: Iterable[tuple[str, ...]] = (),
    ) -> tuple[tuple[str, ...], ...]:
        changes = tuple(changes)
        payload = json.dumps(
            [
                {"path": change.path.replace("\\", "/"),
                 "operation": change.change_type.value}
                for change in changes
            ],
            ensure_ascii=False,
        )
        baseline = (
            "python",
            "-c",
            _FILE_CHECK_SCRIPT,
            str(self.root),
            payload,
        )
        commands: list[tuple[str, ...]] = [baseline]
        commands.extend(self._node_project_commands())

        has_tests = self._has_pytest_tests(changes)
        pytest_added = False
        try:
            existing_files = [item.name for item in self.root.iterdir() if item.is_file()]
        except OSError:
            existing_files = []
        allowed_programs = project_toolchain([change.path for change in changes] + existing_files)
        for raw in suggested_commands:
            # Model-proposed commands are optional hints. Reject malformed
            # entries here as a second boundary, rather than allowing one bad
            # suggestion to poison the deterministic verification set.
            if isinstance(raw, (str, bytes)) or raw is None:
                continue
            try:
                command = tuple(raw)
            except TypeError:
                continue
            if (
                not command
                or any(not isinstance(part, str) or not part.strip() for part in command)
            ):
                continue
            # Same rule as for specification commands: only the project's
            # toolchain, and never several commands joined into one argv.
            if command_rejection(command, allowed_programs):
                continue
            if self._is_pytest(command):
                if has_tests and not pytest_added:
                    commands.append(("python", "-m", "pytest", "-q", "-p", "no:cacheprovider"))
                    pytest_added = True
                # A suggested pytest command is discarded when no test files
                # exist; the deterministic baseline remains meaningful.
                continue
            if command not in commands:
                commands.append(command)

        if has_tests and not pytest_added:
            commands.append(("python", "-m", "pytest", "-q", "-p", "no:cacheprovider"))

        # Add conservative React component-interface analysis for Node projects.
        if (self.root / "package.json").is_file():
            has_frontend_sources = any(
                path.is_file()
                and path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}
                and not any(
                    part in self._IGNORED_DIRS
                    for part in path.relative_to(self.root).parts[:-1]
                )
                for path in self.root.rglob("*")
            )
            if has_frontend_sources:
                commands.append((
                    "python", "-m", "coding_agent.structural_verifier", str(self.root)
                ))

        return tuple(dict.fromkeys(commands))
