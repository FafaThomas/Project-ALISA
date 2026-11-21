
"""Discover and assemble bounded project context for coding tasks."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from coding_agent.models import CodingTask
from coding_agent.workspace import Workspace, WorkspaceError
from coding_agent.code_index import CodeIndex, CodeIndexer


IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    "coverage",
}

IGNORED_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
}

TEXT_EXTENSIONS = {
    ".py", ".pyi",
    ".md", ".rst", ".txt",
    ".toml", ".ini", ".cfg",
    ".json", ".yaml", ".yml",
    ".xml",
    ".sql",
    ".js", ".jsx", ".ts", ".tsx",
    ".html", ".css", ".scss",
    ".sh", ".ps1", ".bat",
    ".go", ".rs",
    ".java", ".kt",
    ".cs",
    ".c", ".h", ".cpp", ".hpp",
    ".dockerfile",
}

PRIORITY_FILENAMES = {
    "readme.md": 100,
    "readme.rst": 100,
    "readme.txt": 100,
    "pyproject.toml": 95,
    "package.json": 95,
    "requirements.txt": 90,
    "requirements-dev.txt": 90,
    "setup.py": 85,
    "setup.cfg": 85,
    "cargo.toml": 95,
    "go.mod": 95,
    "pom.xml": 95,
    "build.gradle": 90,
    "dockerfile": 80,
    "makefile": 80,
    "tsconfig.json": 75,
}


@dataclass(frozen=True)
class ContextFile:
    """A safely retrieved source file included in task context."""

    relative_path: str
    content: str
    size_bytes: int
    modified_ns: int
    relevance_score: int
    truncated: bool = False
    selection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextPackage:
    """A bounded, traceable collection of workspace context."""

    task_id: str
    workspace: str
    files: tuple[ContextFile, ...]
    directory_tree: tuple[str, ...]
    discovered_files: int
    omitted_files: int
    truncated: bool
    code_structure: str = ""

    @property
    def source_paths(self) -> tuple[str, ...]:
        """Return the relative paths used to construct this context."""

        return tuple(item.relative_path for item in self.files)

    @property
    def total_characters(self) -> int:
        """Return the total number of source characters included."""

        return sum(len(item.content) for item in self.files)

    def render(self) -> str:
        """Render context in a deterministic format for an LLM prompt."""

        sections = [
            "PROJECT CONTEXT",
            f"Task ID: {self.task_id}",
            f"Workspace: {self.workspace}",
            f"Discovered text files: {self.discovered_files}",
            f"Included files: {len(self.files)}",
            f"Omitted files: {self.omitted_files}",
            "",
            "DIRECTORY STRUCTURE:",
        ]

        if self.directory_tree:
            sections.extend(self.directory_tree)
        else:
            sections.append("(No directories discovered.)")

        if self.code_structure:
            sections.extend([
                "",
                "CODE STRUCTURE:",
                self.code_structure,
            ])

        sections.extend(["", "SOURCE FILES:"])

        for item in self.files:
            sections.extend(
                [
                    "",
                    f"===== {item.relative_path} =====",
                    item.content,
                ]
            )

            if item.truncated:
                sections.append(
                    "[File content truncated by Context Engine.]"
                )

            if item.selection_reasons:
                sections.append(
                    f"Selection reasons: {', '.join(item.selection_reasons)}"
                )

        if self.truncated:
            sections.extend(
                [
                    "",
                    "[Context package reached its configured size limit.]",
                ]
            )

        return "\n".join(sections)


@dataclass(frozen=True)
class _Candidate:
    """Internal workspace file candidate."""

    relative_path: str
    priority: int
    task_score: int


class ContextEngine:
    """Retrieve relevant project files within an authorized workspace."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        max_files: int = 30,
        max_file_chars: int = 12_000,
        max_context_chars: int = 60_000,
        max_candidates: int = 500,
        max_structure_chars: int = 8_000,
        code_indexer: CodeIndexer | None = None,
    ) -> None:
        if max_files < 1:
            raise ValueError("max_files must be positive")
        if max_file_chars < 1:
            raise ValueError("max_file_chars must be positive")
        if max_context_chars < 1:
            raise ValueError("max_context_chars must be positive")
        if max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if max_structure_chars < 1:
            raise ValueError("max_structure_chars must be positive")

        self.workspace = workspace
        self.max_files = max_files
        self.max_file_chars = max_file_chars
        self.max_context_chars = max_context_chars
        self.max_candidates = max_candidates
        self.max_structure_chars = max_structure_chars

        self.code_indexer = code_indexer or CodeIndexer(
            workspace,
            max_files=max_candidates,
        )

        if self.code_indexer.workspace.root != workspace.root:
            raise WorkspaceError(
                "CodeIndexer workspace does not match ContextEngine workspace"
            )

    
    @staticmethod
    def _tokens(values: Iterable[str]) -> set[str]:
        """Extract normalized search tokens from task text."""

        text = " ".join(values).lower()
        return {
            token
            for token in re.findall(r"[a-z0-9_]{2,}", text)
            if token not in {
                "the", "and", "for", "with", "from", "that",
                "this", "into", "when", "where", "what", "how",
                "are", "was", "were", "will", "can", "should",
                "would", "could", "please", "make", "create",
                "update", "change", "implement",
            }
        }

    @staticmethod
    def _is_text_candidate(path: Path) -> bool:
        """Return whether a file extension is supported for text retrieval."""

        if path.name.lower() in PRIORITY_FILENAMES:
            return True

        if path.name.lower() in IGNORED_FILENAMES:
            return False

        if path.name.lower().startswith(".env"):
            return path.name.lower() in {".env.example", ".env.sample"}

        if path.name.lower() == "dockerfile":
            return True

        return path.suffix.lower() in TEXT_EXTENSIONS

    @staticmethod
    def _task_score(relative_path: str, tokens: set[str]) -> int:
        """Score a path by overlap with task terminology."""

        path_tokens = set(
            re.findall(r"[a-z0-9_]{2,}", relative_path.lower())
        )

        return len(path_tokens & tokens) * 10

    @staticmethod
    def _priority(path: Path) -> int:
        """Assign a baseline priority to project metadata and documentation."""

        name = path.name.lower()

        if name in PRIORITY_FILENAMES:
            return PRIORITY_FILENAMES[name]

        if name.startswith("readme."):
            return 100

        if name in {"requirements", "pipfile", "poetry.lock"}:
            return 80

        if path.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx"}:
            return 20

        if path.suffix.lower() in {".md", ".rst"}:
            return 35

        return 10

    def _discover(
        self,
        tokens: set[str],
    ) -> tuple[list[_Candidate], tuple[str, ...], int]:
        """Discover eligible files without traversing directory symlinks."""

        root = self.workspace.root
        candidates: list[_Candidate] = []
        directories: list[str] = []
        discovered = 0

        for current, dirnames, filenames in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)

            safe_directories = []

            for dirname in sorted(dirnames):
                if dirname.lower() in IGNORED_DIRECTORIES:
                    continue

                candidate_dir = current_path / dirname

                # Do not traverse symlinked directories.
                if candidate_dir.is_symlink():
                    continue

                safe_directories.append(dirname)

                relative_dir = candidate_dir.relative_to(root).as_posix()
                directories.append(relative_dir)

            dirnames[:] = safe_directories

            for filename in sorted(filenames):
                candidate_path = current_path / filename

                if candidate_path.is_symlink():
                    continue

                if not self._is_text_candidate(candidate_path):
                    continue

                try:
                    relative = candidate_path.relative_to(root).as_posix()
                except ValueError:
                    continue

                discovered += 1

                candidates.append(
                    _Candidate(
                        relative_path=relative,
                        priority=self._priority(candidate_path),
                        task_score=self._task_score(relative, tokens),
                    )
                )

                if len(candidates) >= self.max_candidates:
                    break

            if len(candidates) >= self.max_candidates:
                break

        candidates.sort(
            key=lambda item: (
                -(item.priority + item.task_score),
                item.relative_path.lower(),
            )
        )

        return candidates, tuple(sorted(directories)), discovered

    def build(self, task: CodingTask) -> ContextPackage:
        """Build a bounded context package for a coding task."""

        try:
            requested_root = Path(task.workspace).expanduser().resolve(
                strict=True
            )
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError(
                f"Could not resolve task workspace: {exc}"
            ) from exc

        if requested_root != self.workspace.root:
            raise WorkspaceError(
                "Task workspace does not match Context Engine workspace"
            )

        tokens = self._tokens(
            (
                task.description,
                *task.constraints,
                *task.acceptance_criteria,
            )
        )

        candidates, directories, discovered = self._discover(tokens)

        code_index = self.code_indexer.build()

        expanded = self._expand_candidates(
            candidates,
            code_index,
            tokens,
        )

        candidates.sort(
            key=lambda item: (
                -expanded[item.relative_path][0],
                item.relative_path.lower(),
            )
        )

        selected: list[ContextFile] = []
        used_characters = 0
        truncated = False

        for candidate in candidates:
            if len(selected) >= self.max_files:
                break

            remaining = self.max_context_chars - used_characters

            if remaining <= 0:
                truncated = True
                break

            try:
                snapshot = self.workspace.read_text(
                    candidate.relative_path
                )
            except (WorkspaceError, OSError, UnicodeError):
                continue

            content_limit = min(
                self.max_file_chars,
                remaining,
            )

            content = snapshot.content[:content_limit]
            was_truncated = len(snapshot.content) > content_limit

            score, selection_reasons = expanded[
                candidate.relative_path
            ]

            selected.append(
                ContextFile(
                    relative_path=snapshot.relative_path,
                    content=content,
                    size_bytes=snapshot.size_bytes,
                    modified_ns=snapshot.modified_ns,
                    relevance_score=score,
                    truncated=was_truncated,
                    selection_reasons=selection_reasons,
                )
            )

            used_characters += len(content)

            if was_truncated:
                truncated = True

            if used_characters >= self.max_context_chars:
                break

        omitted = max(0, discovered - len(selected))

        if omitted:
            truncated = True

        selected_paths = {
            item.relative_path
            for item in selected
        }

        code_structure, structure_truncated = (
            self._render_code_structure(
                code_index,
                selected_paths,
            )
        )

        truncated = truncated or structure_truncated

        return ContextPackage(
            task_id=task.task_id,
            workspace=str(self.workspace.root),
            files=tuple(selected),
            directory_tree=directories,
            discovered_files=discovered,
            omitted_files=omitted,
            truncated=truncated,
            code_structure=code_structure,
        )

    def _expand_candidates(
        self,
        candidates: list[_Candidate],
        index: CodeIndex,
        task_tokens: set[str],
    ) -> dict[str, tuple[int, tuple[str, ...]]]:
        """Expand relevant Python files through direct dependencies."""

        candidate_lookup = {
            item.relative_path: item
            for item in candidates
        }

        scores = {
            item.relative_path: item.priority + item.task_score
            for item in candidates
        }

        reasons: dict[str, set[str]] = {
            item.relative_path: {"workspace relevance"}
            for item in candidates
        }

        seed_paths = {
            item.relative_path
            for item in candidates
            if Path(item.relative_path).suffix.lower() == ".py"
            and item.task_score > 0
        }

        # Resolve explicitly mentioned symbols to their defining files.
        for symbol in index.symbols:
            symbol_name = symbol.name.lower()

            if symbol_name in task_tokens:
                if symbol.relative_path in candidate_lookup:
                    seed_paths.add(symbol.relative_path)
                    reasons[symbol.relative_path].add(
                        f"defines task-referenced symbol {symbol.name}"
                    )
                    scores[symbol.relative_path] += 80

        modules_by_path = {
            module.relative_path: module
            for module in index.modules
        }

        for seed_path in sorted(seed_paths):
            module = modules_by_path.get(seed_path)

            if module is None or module.parse_error:
                continue

            reasons[seed_path].add("matches task-relevant code")

            for module_name in module.module_names:
                # Outgoing edges: modules this file imports.
                for dependency in index.dependencies_of(module_name):
                    for target in index.find_module(
                        dependency.target_module
                    ):
                        target_path = target.relative_path

                        if target_path not in candidate_lookup:
                            continue

                        reasons[target_path].add(
                            f"dependency of {seed_path}"
                        )
                        scores[target_path] += 40

                # Incoming edges: files that import this module.
                for dependent in index.dependents_of(module_name):
                    dependent_path = dependent.relative_path

                    if dependent_path not in candidate_lookup:
                        continue

                    reasons[dependent_path].add(
                        f"depends on {seed_path}"
                    )
                    scores[dependent_path] += 30

                    # Give test files a small additional relevance boost.
                    name = Path(dependent_path).name.lower()
                    if (
                        name.startswith("test_")
                        or name.endswith("_test.py")
                        or "tests" in Path(dependent_path).parts
                    ):
                        reasons[dependent_path].add(
                            "related test module"
                        )
                        scores[dependent_path] += 15

        return {
            path: (score, tuple(sorted(file_reasons)))
            for path, (score, file_reasons) in (
                (
                    path,
                    (scores[path], reasons[path]),
                )
                for path in scores
            )
        }

    def _render_code_structure(
        self,
        index: CodeIndex,
        selected_paths: set[str],
    ) -> tuple[str, bool]:
        """Render structural information for selected Python files only."""

        selected_modules = [
            module
            for module in index.modules
            if module.relative_path in selected_paths
        ]

        sections = [
            "Selected Python modules and their declarations:"
        ]

        selected_names = {
            alias
            for module in selected_modules
            for alias in module.module_names
        }

        for module in selected_modules:
            primary_name = (
                module.module_names[-1]
                if module.module_names
                else module.relative_path
            )

            sections.append(
                f"\n[{primary_name}] {module.relative_path}"
            )

            if module.parse_error:
                sections.append(
                    f"  Parse error: {module.parse_error}"
                )
                continue

            for symbol in module.symbols:
                sections.append(
                    f"  {symbol.kind}: {symbol.qualified_name} "
                    f"(lines {symbol.line}-{symbol.end_line})"
                )

        sections.append("\nRelevant dependency relationships:")

        for dependency in index.dependencies:
            if dependency.source_module not in selected_names:
                continue

            target_modules = index.find_module(
                dependency.target_module
            )

            target_path = (
                target_modules[0].relative_path
                if target_modules
                else dependency.target_module
            )

            sections.append(
                f"  {dependency.source_module} -> "
                f"{dependency.target_module} "
                f"[{target_path}]"
            )

        rendered = "\n".join(sections)

        if len(rendered) > self.max_structure_chars:
            rendered = (
                rendered[:self.max_structure_chars]
                + "\n[Code structure truncated.]"
            )
            return rendered, True

        return rendered, False


