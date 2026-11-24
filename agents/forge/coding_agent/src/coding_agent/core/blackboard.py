"""The team's shared handoff log, kept as Markdown.

Specialists do not talk to each other directly. Each one reads the blackboard
(what earlier roles decided) and posts its own output to it. The blackboard is
also written to ``.coding-agent/runs/<run_id>/handoff.md`` so a human can read
how the team reached its result.
"""

from __future__ import annotations

from pathlib import Path

from .models import RoleOutput
from .registry import Tree, default_tree


class Blackboard:
    def __init__(self, budget_chars: int = 7000, tree: Tree | None = None) -> None:
        self.entries: list[RoleOutput] = []
        self.budget_chars = budget_chars
        self.tree = tree or default_tree()

    def post(self, output: RoleOutput) -> None:
        self.entries.append(output)

    def _entry_md(self, output: RoleOutput, full: bool) -> str:
        unit = self.tree.by_id.get(output.role)
        lines = [f"#### {unit.name if unit else output.role} ({output.phase})", output.summary or "(no summary)"]
        if output.error:
            lines.append(f"_Error: {output.error}_")
        if full:
            lines += [f"- Decision: {d}" for d in output.decisions]
            lines += [f"- Concern: {c}" for c in output.concerns]
            lines += [
                f"- Work item {w.id} -> {w.owner_role}: {w.title} [{', '.join(w.files) or 'files tbd'}]"
                for w in output.work_items
            ]
            lines += [f"- {c.action} {c.path}" for c in output.changes]
            lines += [f"- [{f.severity}] {f.file or '-'}: {f.issue}" for f in output.findings]
        return "\n".join(lines)

    def render(self) -> str:
        """Newest entries in full; older ones compacted to summaries to fit the budget."""
        if not self.entries:
            return "(no notes yet: you are first)"
        rendered: list[str] = []
        used = 0
        for output in reversed(self.entries):
            text = self._entry_md(output, full=True)
            if used + len(text) > self.budget_chars:
                text = self._entry_md(output, full=False)
                if used + len(text) > self.budget_chars:
                    rendered.append(f"... {len(self.entries) - len(rendered)} earlier note(s) omitted")
                    break
            rendered.append(text)
            used += len(text)
        return "\n\n".join(reversed(rendered))

    def decisions(self) -> list[str]:
        return [d for e in self.entries for d in e.decisions]

    def write(self, path: Path, header: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n\n".join(self._entry_md(e, full=True) for e in self.entries)
        path.write_text(f"{header}\n\n{body}\n", encoding="utf-8")
