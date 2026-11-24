"""Pick the context strategy from the trusted invocation context."""

from __future__ import annotations

from ..models import InvocationContext
from ..workspace import Workspace
from .forge_rag import ForgeRAGContextProvider
from .markdown import MarkdownContextProvider


def build_context_provider(workspace: Workspace, invocation: InvocationContext):
    markdown = MarkdownContextProvider(workspace)
    if invocation.rag_enabled:
        return ForgeRAGContextProvider(
            markdown,
            invocation.knowledge,  # type: ignore[arg-type]
            factions=invocation.knowledge_factions,
            source_id=invocation.knowledge_source_id,
        )
    return markdown
