"""Evidence-preserving relationship resolution for indexed workspaces."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from typing import Mapping, Sequence

from coding_agent.resolvers.defaults import build_default_import_resolver_registry
from coding_agent.resolvers.models import Relationship
from coding_agent.resolvers.registry import ImportResolverRegistry


class RelationshipEngine:
    """Build typed relationships without mutating parser output.

    The engine derives lexical containment and type relationships from parsed
    symbols, delegates imports to language-specific resolvers, and resolves
    parser-emitted references conservatively. Ambiguous and missing targets are
    retained as first-class records instead of being silently discarded.
    """

    _INHERITANCE_KEYS = {
        "extends": "inherits",
        "base_types": "inherits",
        "inherits": "inherits",
        "implements": "implements",
    }
    _PATH_REFERENCE_KINDS = {"html_asset", "stylesheet_import"}
    _REFERENCE_SYMBOL_KINDS = {
        "table_reference": {"table", "view", "cte"},
        "foreign_key": {"table", "view"},
        "index_target": {"table", "view"},
        "mixin_include": {"mixin"},
        "jsx_component": {"class", "function", "variable"},
    }

    def __init__(self, import_resolvers: ImportResolverRegistry | None = None) -> None:
        self.import_resolvers = (
            import_resolvers
            if import_resolvers is not None
            else build_default_import_resolver_registry()
        )

    @staticmethod
    def _module_lookup(modules):
        lookup: dict[str, list] = defaultdict(list)
        for module in modules:
            for alias in module.module_names:
                lookup[alias].append(module)
        return {key: tuple(value) for key, value in lookup.items()}

    @staticmethod
    def _symbol_id(symbol) -> str:
        return f"{symbol.relative_path}::{symbol.qualified_name}@{symbol.line}:{symbol.kind}"

    @staticmethod
    def _module_name(module) -> str:
        return module.module_names[-1] if module.module_names else module.relative_path

    def _resolve_symbol(self, target: str, source_module, symbols_by_name, symbols_by_qualified):
        normalized = target.strip().strip("`\"'").replace("::", ".")
        normalized = re.sub(r"<.*>", "", normalized).strip()
        normalized = normalized.removeprefix("global.").lstrip(".")
        exact = list(symbols_by_qualified.get(normalized, ()))
        exact_same_file = [
            item for item in exact
            if item.relative_path == source_module.relative_path
        ]
        if len(exact_same_file) == 1:
            return "resolved", exact_same_file[0], ()
        if len(exact_same_file) > 1:
            return "ambiguous", None, tuple(sorted(self._symbol_id(item) for item in exact_same_file))
        if len(exact) == 1:
            return "resolved", exact[0], ()
        if len(exact) > 1:
            return "ambiguous", None, tuple(sorted(self._symbol_id(item) for item in exact))

        simple_name = normalized.split(".")[-1]
        candidates = list(symbols_by_name.get(simple_name, ()))
        same_file = [
            item for item in candidates
            if item.relative_path == source_module.relative_path
        ]
        if len(same_file) == 1:
            return "resolved", same_file[0], ()
        if len(same_file) > 1:
            return "ambiguous", None, tuple(sorted(self._symbol_id(item) for item in same_file))
        if len(candidates) == 1:
            return "resolved", candidates[0], ()
        if len(candidates) > 1:
            return "ambiguous", None, tuple(sorted(self._symbol_id(item) for item in candidates))
        return "unresolved", None, ()

    def _containment_relationships(self, modules) -> list[Relationship]:
        relationships = []
        for module in modules:
            if module.parse_error:
                continue
            by_qualified: dict[str, list] = defaultdict(list)
            for symbol in module.symbols:
                by_qualified[symbol.qualified_name].append(symbol)

            for child in module.symbols:
                parts = child.qualified_name.split(".")
                if len(parts) < 2:
                    continue
                parent_name = ".".join(parts[:-1])
                parents = [
                    candidate for candidate in by_qualified.get(parent_name, ())
                    if candidate is not child
                    and candidate.line <= child.line <= candidate.end_line
                ]
                if not parents:
                    continue
                parent = min(
                    parents,
                    key=lambda item: (item.end_line - item.line, item.line),
                )
                relationships.append(Relationship(
                    kind="contains",
                    source_path=module.relative_path,
                    source_module=self._module_name(module),
                    source_symbol=parent.qualified_name,
                    target_path=module.relative_path,
                    target_module=self._module_name(module),
                    target_symbol=child.qualified_name,
                    line=child.line,
                    evidence="qualified_name_scope",
                ))
        return relationships

    @staticmethod
    def _split_type_list(value: str) -> tuple[str, ...]:
        items = []
        depth = 0
        start = 0
        for index, char in enumerate(value):
            if char in "<([":
                depth += 1
            elif char in ">)]":
                depth = max(0, depth - 1)
            elif char == "," and depth == 0:
                items.append(value[start:index].strip())
                start = index + 1
        tail = value[start:].strip()
        if tail:
            items.append(tail)
        return tuple(item for item in items if item)

    def _target_module_name(self, module_by_path, relative_path: str) -> str:
        target_module = module_by_path.get(relative_path)
        if target_module is None:
            return relative_path
        return self._module_name(target_module)

    def _inheritance_relationships(
        self, modules, symbols_by_name, symbols_by_qualified, module_by_path
    ):
        relationships = []
        for module in modules:
            if module.parse_error:
                continue
            for symbol in module.symbols:
                for key, raw_value in symbol.metadata:
                    relationship_kind = self._INHERITANCE_KEYS.get(key)
                    if relationship_kind is None:
                        continue
                    for raw_target in self._split_type_list(raw_value):
                        # C++ base clauses may include access/virtual modifiers.
                        target = re.sub(
                            r"^(?:(?:public|protected|private|virtual)\s+)+", "", raw_target
                        ).strip()
                        status, resolved, candidates = self._resolve_symbol(
                            target, module, symbols_by_name, symbols_by_qualified
                        )
                        relationships.append(Relationship(
                            kind=relationship_kind,
                            source_path=module.relative_path,
                            source_module=self._module_name(module),
                            source_symbol=symbol.qualified_name,
                            target_path=resolved.relative_path if resolved else None,
                            target_module=(
                                self._target_module_name(module_by_path, resolved.relative_path)
                                if resolved else None
                            ),
                            target_symbol=resolved.qualified_name if resolved else target,
                            line=symbol.line,
                            status=status,
                            evidence=f"symbol.metadata:{key}",
                            candidates=candidates,
                        ))
        return relationships

    def _path_reference_relationship(
        self, module, reference, module_by_path
    ) -> Relationship:
        target = reference.target.strip()
        source_name = self._module_name(module)
        if target.startswith(("http://", "https://", "//", "data:")):
            return Relationship(
                kind=reference.kind, source_path=module.relative_path,
                source_module=source_name, source_symbol=reference.context,
                target_path=None, target_module=target, target_symbol=None,
                line=reference.line, status="external", evidence="parser_reference",
            )

        # Remove URL query/fragment portions before resolving a workspace path.
        target_path = target.split("#", 1)[0].split("?", 1)[0]
        if target_path.startswith("/"):
            candidate = target_path.lstrip("/")
        else:
            candidate = posixpath.normpath(
                posixpath.join(posixpath.dirname(module.relative_path), target_path)
            )
        candidates = [candidate]
        if reference.kind == "stylesheet_import" and not posixpath.splitext(candidate)[1]:
            candidates.extend((candidate + ".css", candidate + ".scss"))

        resolved_path = next(
            (item for item in candidates if item in module_by_path),
            None,
        )
        target_module = module_by_path.get(resolved_path) if resolved_path else None
        return Relationship(
            kind=reference.kind,
            source_path=module.relative_path,
            source_module=source_name,
            source_symbol=reference.context,
            target_path=target_module.relative_path if target_module else None,
            target_module=self._module_name(target_module) if target_module else None,
            target_symbol=None if target_module else target,
            line=reference.line,
            status="resolved" if target_module else "unresolved",
            evidence="relative_workspace_path",
        )

    def _reference_relationships(
        self, modules, symbols_by_name, symbols_by_qualified, module_by_path
    ):
        relationships = []
        for module in modules:
            if module.parse_error:
                continue
            for reference in module.references:
                if reference.kind in self._PATH_REFERENCE_KINDS:
                    relationships.append(
                        self._path_reference_relationship(module, reference, module_by_path)
                    )
                    continue
                allowed = self._REFERENCE_SYMBOL_KINDS.get(reference.kind)
                target = reference.target
                if allowed is None:
                    # Unknown reference kinds may represent paths, selectors,
                    # URLs, or language-specific constructs. Preserve them;
                    # do not guess that their text names a symbol.
                    status, resolved, candidates = "unresolved", None, ()
                else:
                    status, resolved, candidates = self._resolve_symbol(
                        target, module, symbols_by_name, symbols_by_qualified
                    )
                    if resolved is not None and resolved.kind not in allowed:
                        status, resolved, candidates = "unresolved", None, ()
                relationships.append(Relationship(
                    kind=reference.kind,
                    source_path=module.relative_path,
                    source_module=self._module_name(module),
                    source_symbol=reference.context,
                    target_path=resolved.relative_path if resolved else None,
                    target_module=(
                        self._target_module_name(module_by_path, resolved.relative_path)
                        if resolved else None
                    ),
                    target_symbol=resolved.qualified_name if resolved else target,
                    line=reference.line,
                    status=status,
                    evidence="parser_reference",
                    candidates=candidates,
                ))
        return relationships

    def resolve(self, modules) -> tuple[Relationship, ...]:
        """Resolve relationships without filesystem-specific language context."""
        return self.resolve_with_context(modules, workspace_root=None)

    def resolve_with_context(self, modules, *, workspace_root=None) -> tuple[Relationship, ...]:
        """Resolve relationships with the workspace root available to adapters."""
        module_lookup = self._module_lookup(modules)
        symbols = [
            symbol
            for module in modules if not module.parse_error
            for symbol in module.symbols
        ]
        symbols_by_name: dict[str, list] = defaultdict(list)
        symbols_by_qualified: dict[str, list] = defaultdict(list)
        for symbol in symbols:
            symbols_by_name[symbol.name].append(symbol)
            symbols_by_qualified[symbol.qualified_name].append(symbol)

        module_by_path = {module.relative_path: module for module in modules}
        relationships = self._containment_relationships(modules)
        relationships.extend(
            self._inheritance_relationships(
                modules, symbols_by_name, symbols_by_qualified, module_by_path
            )
        )
        relationships.extend(
            self._reference_relationships(
                modules, symbols_by_name, symbols_by_qualified, module_by_path
            )
        )

        resolver_contexts = {}
        for language in {item.language for item in modules}:
            resolver = self.import_resolvers.get(language)
            context_factory = getattr(resolver, "create_context", None)
            if context_factory is not None:
                resolver_contexts[language] = context_factory(
                    modules, workspace_root
                )

        for module in modules:
            if module.parse_error:
                continue
            resolver = self.import_resolvers.get(module.language)
            if resolver is not None:
                contextual_resolver = getattr(resolver, "resolve_with_context", None)
                if contextual_resolver is not None:
                    if module.language in resolver_contexts:
                        relationships.extend(
                            contextual_resolver(
                                module,
                                module_lookup,
                                workspace_root=workspace_root,
                                resolution_context=resolver_contexts[module.language],
                            )
                        )
                    else:
                        relationships.extend(
                            contextual_resolver(
                                module, module_lookup, workspace_root=workspace_root
                            )
                        )
                else:
                    relationships.extend(resolver.resolve(module, module_lookup))

        return tuple(sorted(
            relationships,
            key=lambda item: (
                item.source_path, item.line, item.kind,
                item.target_path or "", item.target_symbol or "",
            ),
        ))
