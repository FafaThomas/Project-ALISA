"""Registry for language-specific source parsers."""

from __future__ import annotations

from pathlib import Path

from coding_agent.parsers.base import SourceParser


class ParserRegistry:
    """Resolve parsers by extension or exact filename."""

    def __init__(self, parsers: tuple[SourceParser, ...] | list[SourceParser] = ()) -> None:
        self._parsers: dict[str, SourceParser] = {}
        self._candidates: dict[str, list[SourceParser]] = {}
        self._filenames: dict[str, SourceParser] = {}
        for parser in parsers:
            self.register(parser)

    def register(
        self, parser: SourceParser, *, replace: bool = False, allow_ambiguous: bool = False
    ) -> None:
        if not parser.language.strip():
            raise ValueError("Parser language cannot be empty")
        extensions = tuple(extension.lower() for extension in parser.extensions)
        filenames = tuple(filename.lower() for filename in getattr(parser, "filenames", ()))
        if not extensions and not filenames:
            raise ValueError("Parser must support at least one extension or filename")
        for extension in extensions:
            if not extension.startswith(".") or extension == ".":
                raise ValueError(f"Invalid parser extension: {extension!r}")
            if extension in self._parsers and not (replace or allow_ambiguous):
                raise ValueError(f"A parser is already registered for {extension}")
        for filename in filenames:
            if not filename or "/" in filename or "\\" in filename:
                raise ValueError(f"Invalid parser filename: {filename!r}")
            if filename in self._filenames and not replace:
                raise ValueError(f"A parser is already registered for filename {filename!r}")
        for extension in extensions:
            if replace or extension not in self._parsers:
                self._parsers[extension] = parser
            candidates = self._candidates.setdefault(extension, [])
            if replace:
                candidates[:] = [parser]
            elif parser not in candidates:
                candidates.append(parser)
        for filename in filenames:
            self._filenames[filename] = parser

    def get_parser(
        self, path: str | Path, *, language: str | None = None
    ) -> SourceParser | None:
        candidate = Path(path)
        exact = self._filenames.get(candidate.name.lower())
        if exact is not None:
            return exact if language is None or exact.language == language else None
        candidates = self._candidates.get(candidate.suffix.lower(), [])
        if language is not None:
            return next((item for item in candidates if item.language == language), None)
        return candidates[0] if candidates else None

    def get_candidates(self, path: str | Path) -> tuple[SourceParser, ...]:
        """Return all parsers registered for a path, in registration order."""
        candidate = Path(path)
        exact = self._filenames.get(candidate.name.lower())
        if exact is not None:
            return (exact,)
        return tuple(self._candidates.get(candidate.suffix.lower(), ()))

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return tuple(sorted(self._parsers))

    @property
    def supported_filenames(self) -> tuple[str, ...]:
        return tuple(sorted(self._filenames))
