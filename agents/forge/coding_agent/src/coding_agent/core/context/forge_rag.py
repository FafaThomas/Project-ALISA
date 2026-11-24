"""Forge RAG context: Markdown context plus retrieval from the Forge pipeline.

Only available when the Forge invokes the coding agent and supplies its
retriever. The workspace on disk stays the source of truth for files a
specialist edits; retrieved chunks are reference material.
"""

from __future__ import annotations

import logging

from ..models import KnowledgeRetriever
from ..registry import RoleSpec
from .base import ContextBundle
from .markdown import MarkdownContextProvider, Reader

LOGGER = logging.getLogger(__name__)


class ForgeRAGContextProvider:
    strategy = "forge-rag"

    def __init__(
        self,
        markdown: MarkdownContextProvider,
        retriever: KnowledgeRetriever,
        *,
        factions: tuple[str, ...] = ("forge",),
        source_id: str | None = None,
        top_k: int = 6,
        chunk_chars: int = 2500,
    ) -> None:
        self.markdown = markdown
        self.retriever = retriever
        self.factions = list(factions)
        self.source_id = source_id
        self.top_k = top_k
        self.chunk_chars = chunk_chars

    def _query_for(self, query: str, role: RoleSpec) -> str:
        return f"{query}\n\nPerspective: {role.name} ({role.responsibility})"

    def gather(
        self, query: str, role: RoleSpec, *, focus_files: list[str] | None = None, reader: Reader | None = None
    ) -> ContextBundle:
        bundle = self.markdown.base_bundle(role, focus_files, reader)
        bundle.strategy = self.strategy
        if role.kind == "reviewer":
            return bundle
        try:
            hits = self.retriever.search(
                self._query_for(query, role), self.factions, top_k=self.top_k, source_id=self.source_id
            )
        except Exception as exc:  # the Forge pipeline is optional at runtime
            LOGGER.warning("Forge retrieval failed, falling back to lexical context: %s", exc)
            bundle.add("Retrieval note", f"Forge retrieval was unavailable ({exc}); using lexical ranking.", "", 35)
            fallback = self.markdown.gather(query, role, focus_files=focus_files, reader=reader)
            bundle.sections.extend(s for s in fallback.sections if s.title.startswith("Related source"))
            return bundle

        skip = set(focus_files or [])
        for hit in hits:
            rel = hit.get("relative_path") or hit.get("artifact_id") or "forge knowledge"
            if rel in skip:
                continue
            text = (hit.get("text") or hit.get("summary") or "").strip()
            if not text:
                continue
            score = hit.get("score")
            label = f"{hit.get('faction_id', 'forge')}" + (f", score {score:.2f}" if isinstance(score, (int, float)) else "")
            bundle.add(f"Forge knowledge: {rel}", text[: self.chunk_chars], label, 40)
        return bundle

    def relevant_files(self, query: str, limit: int = 6) -> list[str]:
        paths: list[str] = []
        try:
            for hit in self.retriever.search(query, self.factions, top_k=self.top_k, source_id=self.source_id):
                rel = hit.get("relative_path")
                if rel and rel not in paths and self.markdown.workspace.read_text(rel) is not None:
                    paths.append(rel)
        except Exception as exc:
            LOGGER.warning("Forge retrieval failed while choosing files: %s", exc)
        for rel in self.markdown.relevant_files(query, limit):
            if rel not in paths:
                paths.append(rel)
        return paths[:limit]

    def remember(self, entry_markdown: str) -> None:
        # Durable notes still go to Markdown memory; the Forge owns its own
        # ingestion schedule and can index .coding-agent/memory if it wants.
        self.markdown.remember(entry_markdown)
