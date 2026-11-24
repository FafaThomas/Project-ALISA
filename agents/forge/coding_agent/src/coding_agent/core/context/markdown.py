"""Markdown / workspace context management (no RAG).

Context comes from four places, cheapest first:

1. Memory: ``.coding-agent/memory/CONVENTIONS.md`` (user-maintained) and
   ``PROJECT_MEMORY.md`` (appended by the agent after each run, compacted to
   the most recent entries). This is how decisions survive between runs
   without a vector store.
2. Project docs: README, AGENTS.md, CLAUDE.md, CODING_AGENT.md, CONTRIBUTING.md
   and top-level ``docs/*.md``.
3. A file map of the workspace.
4. Source excerpts ranked by lexical overlap with the task, plus the full
   current content of files a specialist has been assigned.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Callable

from ..registry import RoleSpec
from ..workspace import Workspace
from .base import ContextBundle

DOC_NAMES = ("README.md", "README.MD", "README.rst", "README.txt", "AGENTS.md", "CLAUDE.md",
             "CODING_AGENT.md", "CONTRIBUTING.md", "ARCHITECTURE.md")
SOURCE_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".css", ".scss", ".html",
    ".cs", ".csproj", ".java", ".kt", ".swift", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".rb", ".php",
    ".sql", ".sh", ".ps1", ".toml", ".yaml", ".yml", ".json", ".md", ".ini", ".cfg", ".dart", ".tf",
})
SPECIAL_NAMES = frozenset({"dockerfile", "makefile", "requirements.txt", "package.json", "pyproject.toml"})
MEMORY_FILE = "PROJECT_MEMORY.md"
CONVENTIONS_FILE = "CONVENTIONS.md"
MEMORY_KEEP_ENTRIES = 15
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOP = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "should", "would", "could", "make",
    "add", "use", "using", "new", "create", "build", "write", "implement", "file", "files", "code",
    "please", "want", "need", "have", "will", "can", "are", "not", "all", "each", "when", "then",
})

Reader = Callable[[str], "str | None"]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        parts = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", raw) or [raw]
        for part in {raw, *parts}:
            low = part.lower()
            if len(low) > 2 and low not in _STOP:
                tokens.append(low)
    return tokens


class MarkdownContextProvider:
    strategy = "markdown"

    def __init__(
        self,
        workspace: Workspace,
        *,
        max_excerpt_files: int = 6,
        excerpt_chars: int = 3500,
        focus_file_chars: int = 12000,
        doc_chars: int = 6000,
    ) -> None:
        self.workspace = workspace
        self.max_excerpt_files = max_excerpt_files
        self.excerpt_chars = excerpt_chars
        self.focus_file_chars = focus_file_chars
        self.doc_chars = doc_chars
        self._files: list[str] | None = None

    # ----------------------------------------------------------------- helpers
    @property
    def memory_dir(self):
        return self.workspace.agent_dir / "memory"

    def files(self) -> list[str]:
        if self._files is None:
            self._files = self.workspace.list_files()
        return self._files

    def file_map(self, limit: int = 250) -> str:
        files = self.files()
        if not files:
            return "(empty workspace: this is a new project)"
        lines = files[:limit]
        if len(files) > limit:
            lines.append(f"... and {len(files) - limit} more files")
        return "\n".join(lines)

    def _read_memory(self, name: str) -> str:
        path = self.memory_dir / name
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            return ""

    def _docs(self) -> list[tuple[str, str]]:
        files = set(self.files())
        docs = [name for name in DOC_NAMES if name in files]
        docs += [f for f in self.files() if f.startswith("docs/") and f.count("/") == 1 and f.endswith(".md")][:4]
        out = []
        for rel in docs:
            text = self.workspace.read_text(rel)
            if text:
                out.append((rel, text[: self.doc_chars]))
        return out

    def _is_source(self, rel: str) -> bool:
        path = PurePosixPath(rel)
        return path.suffix.lower() in SOURCE_SUFFIXES or path.name.lower() in SPECIAL_NAMES

    def rank_files(self, query: str, exclude: set[str]) -> list[tuple[float, str]]:
        terms = Counter(tokenize(query))
        if not terms:
            return []
        ranked: list[tuple[float, str]] = []
        for rel in self.files():
            if rel in exclude or not self._is_source(rel):
                continue
            path_tokens = set(tokenize(rel))
            score = sum(3.0 * weight for term, weight in terms.items() if term in path_tokens)
            text = self.workspace.read_text(rel)
            if text:
                lowered = text.lower()
                score += sum(min(lowered.count(term), 5) * 0.5 for term in terms)
            if score > 0:
                ranked.append((score, rel))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked

    def excerpt(self, rel: str, query: str, limit: int) -> str:
        text = self.workspace.read_text(rel) or ""
        if len(text) <= limit:
            return text
        terms = set(tokenize(query))
        lines = text.splitlines()
        best, best_score = 0, -1
        window = max(20, limit // 80)
        for i in range(0, len(lines), max(1, window // 2)):
            chunk = " ".join(lines[i : i + window]).lower()
            score = sum(chunk.count(t) for t in terms)
            if score > best_score:
                best, best_score = i, score
        start = max(0, best - 5)
        body = "\n".join(lines[start:])[:limit]
        return f"[lines {start + 1}+]\n{body}"

    # --------------------------------------------------------------- protocol
    def base_bundle(self, role: RoleSpec, focus_files: list[str] | None, reader: Reader | None) -> ContextBundle:
        bundle = ContextBundle(self.strategy)
        conventions = self._read_memory(CONVENTIONS_FILE)
        bundle.add("Project conventions", conventions, f".coding-agent/memory/{CONVENTIONS_FILE}", 5)
        if role.kind in ("analyst", "planner", "designer", "coordinator"):
            memory = self._read_memory(MEMORY_FILE)
            bundle.add("Decisions from earlier runs", memory, f".coding-agent/memory/{MEMORY_FILE}", 15)
            for rel, text in self._docs():
                bundle.add(f"Project doc {rel}", text, rel, 20)
        bundle.add("Workspace file map", self.file_map(), "workspace", 30)
        read = reader or self.workspace.read_text
        for rel in focus_files or []:
            content = read(rel)
            if content is None:
                bundle.add(f"Assigned file {rel}", "(does not exist yet: create it)", rel, 10)
            else:
                body = content if len(content) <= self.focus_file_chars else \
                    content[: self.focus_file_chars] + "\n... [truncated]"
                bundle.add(f"Assigned file {rel} (current content)", body, rel, 10)
        return bundle

    def gather(
        self, query: str, role: RoleSpec, *, focus_files: list[str] | None = None, reader: Reader | None = None
    ) -> ContextBundle:
        bundle = self.base_bundle(role, focus_files, reader)
        if role.kind == "reviewer":
            return bundle  # reviewers get the proposed changes directly
        exclude = set(focus_files or [])
        for score, rel in self.rank_files(query, exclude)[: self.max_excerpt_files]:
            bundle.add(f"Related source {rel}", self.excerpt(rel, query, self.excerpt_chars), f"score {score:.1f}", 40)
        return bundle

    def relevant_files(self, query: str, limit: int = 6) -> list[str]:
        """Workspace files most related to ``query`` (for reviewing existing code)."""
        ranked = [rel for _, rel in self.rank_files(query, set())]
        if len(ranked) < limit:
            ranked += [f for f in self.files() if self._is_source(f) and f not in ranked]
        return ranked[:limit]

    def remember(self, entry_markdown: str) -> None:
        """Append a run summary to PROJECT_MEMORY.md, keeping recent entries only."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        existing = self._read_memory(MEMORY_FILE)
        header = "# Coding agent project memory\n\nMost recent runs last. Older entries are compacted away.\n"
        entries = [e for e in existing.split("\n## ")[1:]] if existing else []
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entries.append(f"{stamp}\n{entry_markdown.strip()}\n")
        entries = entries[-MEMORY_KEEP_ENTRIES:]
        text = header + "".join(f"\n## {e.rstrip()}\n" for e in entries)
        (self.memory_dir / MEMORY_FILE).write_text(text, encoding="utf-8")
