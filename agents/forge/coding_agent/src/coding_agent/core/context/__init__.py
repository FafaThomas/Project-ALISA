"""Context management for the coding team.

Two strategies share one interface:

- ``MarkdownContextProvider``: used when the coding agent is invoked directly
  (API, ALISA UI, CLI). Context comes from workspace Markdown docs, a file map,
  lexically ranked source excerpts, and the agent's own Markdown memory files
  under ``.coding-agent/memory``.
- ``ForgeRAGContextProvider``: used only when the Forge invokes the agent and
  hands over its retriever. It keeps the Markdown layer and adds retrieved
  Forge knowledge.
"""

from .base import ContextBundle, ContextProvider, ContextSection
from .factory import build_context_provider
from .forge_rag import ForgeRAGContextProvider
from .markdown import MarkdownContextProvider

__all__ = [
    "ContextBundle",
    "ContextProvider",
    "ContextSection",
    "ForgeRAGContextProvider",
    "MarkdownContextProvider",
    "build_context_provider",
]
