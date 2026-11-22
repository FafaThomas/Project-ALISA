"""Contracts shared by language-specific import resolvers."""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence, TYPE_CHECKING

from coding_agent.resolvers.models import Relationship

if TYPE_CHECKING:
    from coding_agent.code_index import ModuleRecord


class ImportResolver(Protocol):
    """Resolve one language's import records against indexed modules."""

    language: str

    def resolve(
        self,
        module: "ModuleRecord",
        module_lookup: Mapping[str, Sequence["ModuleRecord"]],
    ) -> tuple[Relationship, ...]:
        ...
