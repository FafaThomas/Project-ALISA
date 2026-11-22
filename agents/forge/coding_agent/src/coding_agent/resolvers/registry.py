"""Registry for language-specific import-resolution strategies."""

from __future__ import annotations

from coding_agent.resolvers.base import ImportResolver


class ImportResolverRegistry:
    """Select import resolution behavior by the parser's language ID."""

    def __init__(self) -> None:
        self._resolvers: dict[str, ImportResolver] = {}

    def register(self, resolver: ImportResolver) -> None:
        language = resolver.language.strip().lower()
        if not language:
            raise ValueError("Resolver language cannot be empty")
        if language in self._resolvers:
            raise ValueError(f"An import resolver is already registered for {language!r}")
        self._resolvers[language] = resolver

    def get(self, language: str) -> ImportResolver | None:
        return self._resolvers.get(language.strip().lower())

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._resolvers))
