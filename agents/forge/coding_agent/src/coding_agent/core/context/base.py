"""Context provider contract and the bundle it returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..registry import RoleSpec


@dataclass
class ContextSection:
    title: str
    body: str
    source: str = ""
    priority: int = 50  # lower renders first and is trimmed last


@dataclass
class ContextBundle:
    strategy: str
    sections: list[ContextSection] = field(default_factory=list)

    def add(self, title: str, body: str, source: str = "", priority: int = 50) -> None:
        if body and body.strip():
            self.sections.append(ContextSection(title, body.strip(), source, priority))

    def render(self, budget_chars: int) -> str:
        """Render sections in priority order, trimming to the character budget."""
        out: list[str] = []
        remaining = budget_chars
        for section in sorted(self.sections, key=lambda s: s.priority):
            header = f"### {section.title}" + (f"  ({section.source})" if section.source else "")
            cost = len(header) + 2
            if remaining <= cost + 80:
                out.append(f"### (omitted: {section.title}; context budget reached)")
                continue
            body = section.body
            if len(body) + cost > remaining:
                body = body[: remaining - cost - 40].rstrip() + "\n... [truncated]"
            out.append(f"{header}\n{body}")
            remaining -= len(body) + cost
        return "\n\n".join(out)


class ContextProvider(Protocol):
    strategy: str

    def gather(
        self,
        query: str,
        role: RoleSpec,
        *,
        focus_files: list[str] | None = None,
        reader: Callable[[str], str | None] | None = None,
    ) -> ContextBundle:
        """Context for one specialist working on ``query``.

        ``reader`` returns a file's content as the team currently sees it
        (proposed changes included); it defaults to reading the workspace.
        """
        ...

    def relevant_files(self, query: str, limit: int = 6) -> list[str]:
        """Existing workspace files most related to ``query``."""
        ...

    def remember(self, entry_markdown: str) -> None:
        """Persist a durable note for future runs on this workspace."""
        ...
