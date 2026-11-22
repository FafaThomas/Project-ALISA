"""Resolve relative JavaScript/TypeScript module specifiers to workspace files."""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath
from typing import Mapping, Sequence, TYPE_CHECKING
from urllib.parse import urlsplit

from coding_agent.resolvers.models import Relationship

if TYPE_CHECKING:
    from coding_agent.code_index import ModuleRecord


class RelativePathImportResolver:
    """Resolve only path-based imports; package and alias imports stay explicit."""

    _EXTENSIONS_BY_LANGUAGE = {
        "javascript": (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".json"),
        "jsx": (".jsx", ".js", ".tsx", ".ts", ".mjs", ".cjs", ".mts", ".cts", ".json"),
        "typescript": (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".json"),
        "tsx": (".tsx", ".ts", ".jsx", ".js", ".mts", ".cts", ".mjs", ".cjs", ".json"),
    }

    def __init__(self, language: str) -> None:
        normalized = language.strip().lower()
        if normalized not in self._EXTENSIONS_BY_LANGUAGE:
            raise ValueError(f"Unsupported relative-import language: {language!r}")
        self.language = normalized
        self.extensions = self._EXTENSIONS_BY_LANGUAGE[normalized]

    @staticmethod
    def _all_modules(module_lookup: Mapping[str, Sequence["ModuleRecord"]]):
        modules = {}
        for group in module_lookup.values():
            for module in group:
                modules[module.relative_path] = module
        return modules

    def _candidate_paths(self, source_path: str, specifier: str) -> tuple[str, ...]:
        clean = urlsplit(specifier).path
        if clean.startswith("/"):
            base = clean.lstrip("/")
        else:
            base = posixpath.normpath(
                posixpath.join(str(PurePosixPath(source_path).parent), clean)
            )
        if base == ".." or base.startswith("../"):
            return ()

        candidates = [base]
        if not PurePosixPath(base).suffix:
            candidates.extend(base + extension for extension in self.extensions)
            candidates.extend(
                posixpath.join(base, "index" + extension)
                for extension in self.extensions
            )
        return tuple(dict.fromkeys(candidates))

    def resolve(
        self,
        module: "ModuleRecord",
        module_lookup: Mapping[str, Sequence["ModuleRecord"]],
    ) -> tuple[Relationship, ...]:
        if not module.module_names:
            return ()
        all_modules = self._all_modules(module_lookup)
        source_name = module.module_names[-1]
        relationships = []

        for imported in module.imports:
            specifier = imported.module or ""
            if specifier.startswith(("http://", "https://", "//")):
                relationships.append(Relationship(
                    kind="imports", source_path=module.relative_path,
                    source_module=source_name, source_symbol=None,
                    target_path=None, target_module=specifier, target_symbol=None,
                    line=imported.line, status="external", evidence=imported.kind,
                    metadata=(("imported_names", ",".join(imported.imported_names)),),
                ))
                continue

            if not specifier.startswith((".", "/")):
                # Bare package specifiers (e.g. react, lodash, node:fs) are
                # outside the relative-path resolver's scope. Path aliases such
                # as @/components are not classified as external because their
                # meaning depends on project configuration.
                status = "external" if not specifier.startswith(("@/", "~/", "#", "src/")) else "unresolved"
                relationships.append(Relationship(
                    kind="imports", source_path=module.relative_path,
                    source_module=source_name, source_symbol=None,
                    target_path=None, target_module=specifier or None, target_symbol=None,
                    line=imported.line, status=status, evidence=imported.kind,
                    metadata=(("imported_names", ",".join(imported.imported_names)),),
                ))
                continue

            candidates = self._candidate_paths(module.relative_path, specifier)
            # Candidate order expresses extension and index-file precedence.
            selected_path = next((path for path in candidates if path in all_modules), None)
            if selected_path is None:
                relationships.append(Relationship(
                    kind="imports", source_path=module.relative_path,
                    source_module=source_name, source_symbol=None,
                    target_path=None, target_module=specifier, target_symbol=None,
                    line=imported.line, status="unresolved", evidence=imported.kind,
                    metadata=(("imported_names", ",".join(imported.imported_names)),),
                ))
                continue

            target_module = all_modules[selected_path]
            relationships.append(Relationship(
                kind="imports", source_path=module.relative_path,
                source_module=source_name, source_symbol=None,
                target_path=target_module.relative_path,
                target_module=(target_module.module_names[-1] if target_module.module_names else target_module.relative_path),
                target_symbol=None, line=imported.line, status="resolved",
                evidence=imported.kind,
                metadata=(("imported_names", ",".join(imported.imported_names)),),
            ))

        return tuple(relationships)
