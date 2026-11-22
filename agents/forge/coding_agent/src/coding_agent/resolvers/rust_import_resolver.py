"""Resolve Rust module and Cargo dependency relationships statically.

The resolver deliberately separates Cargo package dependencies from Rust
module/use paths. It does not invoke Cargo, access the network, or assume that
an unknown first path segment is an external crate.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence, TYPE_CHECKING

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from coding_agent.resolvers.models import Relationship

if TYPE_CHECKING:
    from coding_agent.code_index import ModuleRecord


_IGNORED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    "target", "vendor", "dist", "build", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox",
}
_STANDARD_CRATES = frozenset({"std", "core", "alloc", "proc_macro", "test"})
_DEPENDENCY_SECTIONS = (
    "dependencies", "dev-dependencies", "build-dependencies",
)


@dataclass(frozen=True)
class CargoDependency:
    alias: str
    package_name: str
    path: str | None = None
    version: str | None = None
    kind: str = "dependencies"
    path_base: str = ""

    @property
    def crate_name(self) -> str:
        return self.alias.replace("-", "_")


@dataclass(frozen=True)
class RustPackage:
    root: str
    manifest_path: str
    name: str
    crate_name: str
    edition: str
    lib_target: str | None
    targets: tuple[tuple[str, str], ...]
    dependencies: tuple[CargoDependency, ...]


@dataclass(frozen=True)
class RustCrateContext:
    package: RustPackage
    target_name: str
    root_file: str
    module_files: Mapping[tuple[str, ...], tuple[str, ...]]
    module_symbols: Mapping[str, tuple[object, ...]]
    module_relationships: tuple[Relationship, ...]


@dataclass(frozen=True)
class RustResolverContext:
    root: Path
    packages: tuple[RustPackage, ...]
    crate_contexts: tuple[RustCrateContext, ...]
    file_contexts: Mapping[str, tuple[RustCrateContext, ...]]
    package_by_root: Mapping[str, RustPackage]


class RustImportResolver:
    language = "rust"

    def resolve(self, module, module_lookup):
        return self.resolve_with_context(module, module_lookup, workspace_root=None)

    def create_context(self, modules, workspace_root) -> RustResolverContext | None:
        if workspace_root is None:
            return None
        root = Path(workspace_root).resolve()
        indexed = {
            item.relative_path: item
            for item in modules
            if item.language == "rust" and item.parse_error is None
        }
        packages = self._discover_packages(root)
        package_by_root = {item.root: item for item in packages}
        crate_contexts: list[RustCrateContext] = []
        file_contexts: dict[str, list[RustCrateContext]] = {}

        for package in packages:
            for target_name, root_file in package.targets:
                if root_file not in indexed:
                    continue
                context = self._build_crate_context(
                    root, package, target_name, root_file, indexed
                )
                crate_contexts.append(context)
                paths_for_context = {
                    path for paths in context.module_files.values() for path in paths
                }
                for path in paths_for_context:
                    file_contexts.setdefault(path, []).append(context)

        return RustResolverContext(
            root=root,
            packages=packages,
            crate_contexts=tuple(crate_contexts),
            file_contexts={key: tuple(value) for key, value in file_contexts.items()},
            package_by_root=package_by_root,
        )

    def resolve_with_context(
        self,
        module: "ModuleRecord",
        module_lookup: Mapping[str, Sequence["ModuleRecord"]],
        *,
        workspace_root=None,
        resolution_context: RustResolverContext | None = None,
    ) -> tuple[Relationship, ...]:
        if workspace_root is None:
            return tuple(
                self._unresolved(module, item, "workspace_root_unavailable")
                for item in module.imports
            )
        context = resolution_context or self.create_context(
            self._indexed_modules(module_lookup), workspace_root
        )
        if context is None:
            return ()

        relationships: list[Relationship] = []
        source_contexts = context.file_contexts.get(module.relative_path, ())
        if len(source_contexts) > 1:
            # A shared Rust source file can be compiled into multiple targets.
            # Resolve only if all target contexts agree on the destination.
            for imported in module.imports:
                relationships.append(self._unresolved(
                    module, imported, "source_file_belongs_to_multiple_crates",
                    status="ambiguous",
                ))
            return tuple(relationships)

        source_context = source_contexts[0] if source_contexts else None
        if source_context is not None:
            relationships.extend(
                item for item in source_context.module_relationships
                if item.source_path == module.relative_path
            )

        for imported in module.imports:
            relationships.extend(self._resolve_use(
                module, imported, source_context, context
            ))

        # Cargo package dependencies are a distinct relationship layer. Emit
        # each package edge once, from the manifest rather than duplicating it
        # for every source file in the crate.
        if source_context and module.relative_path == source_context.root_file:
            package_targets = source_context.package.targets
            primary_target = (
                "lib" if source_context.package.lib_target
                else (package_targets[0][0] if package_targets else None)
            )
            if source_context.target_name == primary_target:
                relationships.extend(self._cargo_dependency_relationships(
                    module, source_context.package, context
                ))

        return tuple(relationships)

    @staticmethod
    def _indexed_modules(module_lookup):
        unique = {}
        for group in module_lookup.values():
            for item in group:
                unique[item.relative_path] = item
        return tuple(unique.values())

    @staticmethod
    def _read_toml(path: Path) -> dict:
        try:
            with path.open("rb") as stream:
                value = tomllib.load(stream)
            return value if isinstance(value, dict) else {}
        except (OSError, UnicodeError, ValueError, TypeError):
            return {}

    @classmethod
    def _discover_packages(cls, root: Path) -> tuple[RustPackage, ...]:
        manifests: list[Path] = []
        for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            dirnames[:] = [
                name for name in dirnames
                if name.lower() not in _IGNORED_DIRS
                and not (current_path / name).is_symlink()
            ]
            if "Cargo.toml" in filenames:
                manifest = current_path / "Cargo.toml"
                if not manifest.is_symlink():
                    manifests.append(manifest)

        manifest_data = {path: cls._read_toml(path) for path in manifests}
        workspace_dependencies: dict[str, dict] = {}
        workspace_package: dict[str, object] = {}
        for path, data in manifest_data.items():
            if path.parent == root:
                workspace_data = data.get("workspace", {}) or {}
                workspace_dependencies = workspace_data.get("dependencies", {}) or {}
                workspace_package = workspace_data.get("package", {}) or {}
                break

        packages: list[RustPackage] = []
        for manifest in sorted(manifests):
            data = manifest_data[manifest]
            package_data = data.get("package") or {}
            name = package_data.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            package_root = manifest.parent.resolve()
            try:
                relative_root = package_root.relative_to(root).as_posix()
            except ValueError:
                continue
            if relative_root == ".":
                relative_root = ""
            default_crate_name = name.replace("-", "_")
            lib_data = data.get("lib") or {}
            crate_name = str(lib_data.get("name", default_crate_name)).replace("-", "_")
            edition_value = package_data.get("edition", "2015")
            if isinstance(edition_value, dict) and edition_value.get("workspace") is True:
                edition_value = workspace_package.get("edition", "2015")
            edition = str(edition_value)
            targets = cls._targets(root, manifest, data, name, crate_name)
            lib_target = next((path for kind, path in targets if kind == "lib"), None)
            dependencies = cls._dependencies(data, workspace_dependencies, relative_root)
            packages.append(RustPackage(
                root=relative_root,
                manifest_path=(Path(relative_root) / "Cargo.toml").as_posix()
                    if relative_root else "Cargo.toml",
                name=name,
                crate_name=crate_name,
                edition=edition,
                lib_target=lib_target,
                targets=tuple(targets),
                dependencies=tuple(dependencies),
            ))
        return tuple(packages)

    @staticmethod
    def _safe_relative(root: Path, path: Path) -> str | None:
        try:
            resolved = path.resolve()
            return resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            return None

    @classmethod
    def _targets(cls, root: Path, manifest: Path, data: dict, package_name: str, crate_name: str):
        package_root = manifest.parent
        targets: list[tuple[str, str]] = []
        lib_data = data.get("lib") or {}
        lib_path = lib_data.get("path", "src/lib.rs")
        resolved_lib = cls._safe_relative(root, package_root / lib_path)
        if resolved_lib and (root / resolved_lib).is_file():
            targets.append(("lib", resolved_lib))

        explicit_bins = data.get("bin") or []
        explicit_bin_paths = set()
        for item in explicit_bins:
            path = item.get("path")
            name = str(item.get("name", package_name)).replace("-", "_")
            if path:
                resolved = cls._safe_relative(root, package_root / path)
                if resolved and (root / resolved).is_file():
                    targets.append((name, resolved))
                    explicit_bin_paths.add(resolved)

        autobins = (data.get("package") or {}).get("autobins", True)
        if autobins:
            default_main = cls._safe_relative(root, package_root / "src" / "main.rs")
            if default_main and (root / default_main).is_file() and default_main not in explicit_bin_paths:
                targets.append((package_name.replace("-", "_"), default_main))
            bin_dir = package_root / "src" / "bin"
            if bin_dir.is_dir():
                for path in sorted(bin_dir.rglob("*.rs")):
                    if path.is_symlink() or any(part in _IGNORED_DIRS for part in path.parts):
                        continue
                    if path.name == "main.rs":
                        name = path.parent.name
                    elif path.parent == bin_dir:
                        name = path.stem
                    else:
                        name = path.relative_to(bin_dir).with_suffix("").as_posix().replace("/", "_")
                    resolved = cls._safe_relative(root, path)
                    if resolved and resolved not in explicit_bin_paths:
                        targets.append((name.replace("-", "_"), resolved))
        return list(dict.fromkeys(targets))

    @classmethod
    def _dependencies(cls, data: dict, workspace_dependencies: dict, package_root: str) -> list[CargoDependency]:
        result = []
        sections = []
        for name in _DEPENDENCY_SECTIONS:
            sections.append((name, data.get(name) or {}))
        for target_data in (data.get("target") or {}).values():
            for name in _DEPENDENCY_SECTIONS:
                sections.append((name, target_data.get(name) or {}))

        seen = set()
        for kind, entries in sections:
            if not isinstance(entries, dict):
                continue
            for alias, spec in entries.items():
                if (kind, alias) in seen:
                    continue
                seen.add((kind, alias))
                if isinstance(spec, str):
                    spec = {"version": spec}
                if not isinstance(spec, dict):
                    continue
                path_base = package_root
                if spec.get("workspace") is True:
                    inherited = workspace_dependencies.get(alias, {})
                    if isinstance(inherited, str):
                        inherited = {"version": inherited}
                    if isinstance(inherited, dict):
                        spec = {**inherited, **{k: v for k, v in spec.items() if k != "workspace"}}
                        path_base = ""
                package_name = str(spec.get("package", alias))
                result.append(CargoDependency(
                    alias=str(alias),
                    package_name=package_name,
                    path=str(spec["path"]) if spec.get("path") else None,
                    version=str(spec["version"]) if spec.get("version") else None,
                    kind=kind,
                    path_base=path_base,
                ))
        return result

    @staticmethod
    def _metadata(symbol) -> dict[str, str]:
        return dict(symbol.metadata)

    def _build_crate_context(self, root, package, target_name, root_file, indexed):
        root_path = PurePosixPath(root_file)
        module_base = root_path.parent
        modules: dict[tuple[str, ...], list[str]] = {(): [root_file]}
        queue = [(root_file, ())]
        seen_contexts = set()
        relationships: list[Relationship] = []
        while queue:
            current_path, current_module_path = queue.pop(0)
            context_key = (current_path, current_module_path)
            if context_key in seen_contexts:
                continue
            seen_contexts.add(context_key)
            record = indexed.get(current_path)
            if record is None:
                continue
            for symbol in record.symbols:
                if symbol.kind != "module":
                    continue
                metadata = self._metadata(symbol)
                module_form = metadata.get("module_form", "external")
                declared_parts = tuple(part for part in symbol.qualified_name.split(".") if part)
                target_module_path = current_module_path + declared_parts
                if not target_module_path:
                    continue
                if module_form == "inline":
                    candidates = (current_path,)
                    evidence = "rust_inline_module"
                else:
                    custom_path = None
                    for attribute in symbol.decorators:
                        match = re.search(r'#\s*\[\s*path\s*=\s*["\']([^"\']+)["\']\s*\]', attribute)
                        if match:
                            custom_path = match.group(1)
                            break
                    if custom_path:
                        custom_base = PurePosixPath(*module_base.parts, *target_module_path[:-1])
                        candidate = (custom_base / custom_path)
                        candidate = Path(root, *candidate.parts).resolve()
                        try:
                            candidate_rel = candidate.relative_to(root).as_posix()
                        except ValueError:
                            candidate_rel = ""
                        candidates = (candidate_rel,) if candidate_rel in indexed else ()
                    else:
                        stem = PurePosixPath(*module_base.parts, *target_module_path)
                        first = stem.with_suffix(".rs").as_posix()
                        second = (stem / "mod.rs").as_posix()
                        candidates = tuple(path for path in (first, second) if path in indexed)
                    evidence = "rust_module_declaration"

                unique_candidates = tuple(dict.fromkeys(candidates))
                modules.setdefault(target_module_path, [])
                for candidate in unique_candidates:
                    if candidate not in modules[target_module_path]:
                        modules[target_module_path].append(candidate)
                    if module_form != "inline":
                        queue.append((candidate, target_module_path))
                if module_form == "inline":
                    status = "resolved"
                elif len(unique_candidates) == 1:
                    status = "resolved"
                elif len(unique_candidates) > 1:
                    status = "ambiguous"
                else:
                    status = "unresolved"
                relationships.append(Relationship(
                    kind="declares_module",
                    source_path=current_path,
                    source_module=f"{package.crate_name}::{target_name}",
                    source_symbol=symbol.qualified_name,
                    target_path=unique_candidates[0] if len(unique_candidates) == 1 else None,
                    target_module="::".join(target_module_path),
                    target_symbol=None,
                    line=symbol.line,
                    status=status,
                    evidence=evidence,
                    candidates=unique_candidates if len(unique_candidates) > 1 else (),
                    metadata=(("module_form", module_form),),
                ))

        module_symbols = {
            path: tuple(indexed[path].symbols) if path in indexed else ()
            for paths in modules.values() for path in paths
        }
        return RustCrateContext(
            package=package,
            target_name=target_name,
            root_file=root_file,
            module_files={key: tuple(value) for key, value in modules.items()},
            module_symbols=module_symbols,
            module_relationships=tuple(relationships),
        )

    def _cargo_dependency_relationships(self, source_module, package, context):
        relationships = []
        for dependency in package.dependencies:
            target_package = None
            status = "external"
            target_path = None
            evidence = "cargo_registry_dependency"
            reason = None
            if dependency.path:
                candidate = (context.root / dependency.path_base / dependency.path).resolve()
                try:
                    relative = candidate.relative_to(context.root).as_posix()
                    if relative == ".":
                        relative = ""
                except ValueError:
                    relative = None
                if relative is None:
                    status, reason = "unresolved", "local_dependency_outside_workspace"
                    evidence = "cargo_local_path_dependency"
                else:
                    target_package = context.package_by_root.get(relative)
                    if target_package and target_package.name == dependency.package_name:
                        status = "resolved"
                        target_path = target_package.manifest_path
                        evidence = "cargo_local_path_dependency"
                    else:
                        status, reason = "unresolved", "local_dependency_manifest_not_found"
                        evidence = "cargo_local_path_dependency"
            metadata = [
                ("dependency_alias", dependency.alias),
                ("package_name", dependency.package_name),
                ("dependency_kind", dependency.kind),
            ]
            if dependency.version:
                metadata.append(("version", dependency.version))
            if reason:
                metadata.append(("reason", reason))
            relationships.append(Relationship(
                kind="cargo_dependency",
                source_path=package.manifest_path,
                source_module=package.name,
                source_symbol=None,
                target_path=target_path,
                target_module=dependency.package_name,
                target_symbol=None,
                line=0,
                status=status,
                evidence=evidence,
                metadata=tuple(metadata),
            ))
        return relationships

    def _resolve_use(self, module, imported, source_context, context):
        source_name = (
            f"{source_context.package.crate_name}::{source_context.target_name}"
            if source_context is not None
            else (module.module_names[-1] if module.module_names else module.relative_path)
        )
        path = (imported.module or "").strip()
        names = imported.imported_names
        aliases = dict(imported.aliases)
        metadata = [
            ("imported_names", ",".join(names)),
            ("import_alias", imported.alias or ""),
            ("import_kind", imported.kind),
            ("is_public", str(imported.is_public).lower()),
            ("is_wildcard", str(imported.is_wildcard).lower()),
        ]
        if aliases:
            metadata.append(("aliases", ",".join(f"{a}={b}" for a, b in aliases.items())))
        if imported.alias:
            metadata.append(("import_semantics", "aliased_import"))
        if imported.is_wildcard:
            metadata.append(("import_semantics", "glob_import"))

        if not path:
            return [self._unresolved(
                module, imported, "empty_rust_use_path", metadata=tuple(metadata),
                source_module=source_name,
            )]

        parts = tuple(part for part in path.split("::") if part)
        if not parts:
            return [self._unresolved(
                module, imported, "empty_rust_use_path", metadata=tuple(metadata),
                source_module=source_name,
            )]

        if parts[0] in _STANDARD_CRATES:
            return [self._external_use(
                module, imported, "::".join(parts), "rust_standard_crate", metadata,
                source_module=source_name,
            )]

        if source_context is None:
            return [self._unresolved(
                module, imported, "source_crate_context_unavailable",
                metadata=tuple(metadata), source_module=source_name,
            )]

        package = source_context.package
        dependency = next((item for item in package.dependencies if item.crate_name == parts[0]), None)
        if dependency is not None:
            if dependency.path:
                target_package = self._local_dependency(package, dependency, context)
                if target_package is None:
                    return [self._unresolved(
                        module, imported, "local_dependency_unavailable", metadata=tuple(metadata),
                        target_module="::".join(parts), source_module=source_name,
                    )]
                target_crates = [item for item in context.crate_contexts
                                 if item.package.root == target_package.root and item.target_name == "lib"]
                if not target_crates:
                    return [self._unresolved(
                        module, imported, "local_dependency_library_not_indexed",
                        metadata=tuple(metadata), target_module="::".join(parts),
                        source_module=source_name,
                    )]
                return self._resolve_in_crate(
                    module, imported, target_crates[0], parts[1:], (), metadata,
                    evidence="rust_local_crate_dependency",
                )
            return [self._external_use(
                module, imported, "::".join(parts), "cargo_declared_external_crate", metadata,
                source_module=source_name,
            )]

        if parts[0] in {"crate", "self", "super"} or parts[0] == "":
            return self._resolve_in_crate(
                module, imported, source_context, parts, (), metadata,
                evidence="rust_module_path",
            )

        # Rust 2018+ paths may start at the crate root. A bare path is only
        # treated as local when the module tree proves that it exists.
        if package.edition != "2015":
            local_parts = parts
            if local_parts in source_context.module_files:
                return self._resolve_in_crate(
                    module, imported, source_context, local_parts, (), metadata,
                    evidence="rust_crate_root_path",
                )
            if local_parts[:-1] in source_context.module_files:
                return self._resolve_in_crate(
                    module, imported, source_context, local_parts[:-1], (), metadata,
                    evidence="rust_crate_root_path",
                )
        return [self._unresolved(
            module, imported, "crate_not_declared_as_dependency_or_local_module",
            metadata=tuple(metadata), target_module="::".join(parts),
            source_module=source_name,
        )]

    def _resolve_in_crate(self, module, imported, crate_context, parts, _unused, metadata, evidence):
        package = crate_context.package
        current_path = self._module_path_for_source(module.relative_path, crate_context)
        segments = list(parts)
        if segments and segments[0] == "crate":
            module_path = tuple(segments[1:])
        elif segments and segments[0] == "self":
            module_path = current_path + tuple(segments[1:])
        elif segments and segments[0] == "super":
            parent = current_path
            while segments and segments[0] == "super":
                parent = parent[:-1]
                segments.pop(0)
            module_path = parent + tuple(segments)
        else:
            module_path = tuple(segments)

        base_candidates = crate_context.module_files.get(module_path, ())
        if len(base_candidates) > 1:
            return [self._ambiguous_use(
                module, imported, module_path, base_candidates, metadata, evidence,
                source_module=f"{package.crate_name}::{crate_context.target_name}",
            )]
        if not base_candidates:
            return [self._unresolved(
                module, imported, "rust_module_path_not_indexed",
                metadata=tuple(metadata), target_module="::".join(module_path),
                source_module=f"{package.crate_name}::{crate_context.target_name}",
            )]

        base_file = base_candidates[0]
        names = imported.imported_names
        if not names or imported.is_wildcard:
            return [Relationship(
                kind="imports",
                source_path=module.relative_path,
                source_module=f"{package.crate_name}::{crate_context.target_name}",
                source_symbol=None,
                target_path=base_file,
                target_module="::".join(module_path) or package.crate_name,
                target_symbol=None,
                line=imported.line,
                status="resolved",
                evidence=evidence,
                metadata=tuple(metadata),
            )]

        relationships = []
        for name in names:
            if name == "self":
                child_path = module_path
                symbol_candidates = ()
            else:
                child_path = module_path + (name,)
                symbol_candidates = crate_context.module_symbols.get(base_file, ())
            child_files = crate_context.module_files.get(child_path, ()) if name != "self" else (base_file,)
            item_metadata = list(metadata)
            item_metadata.append(("imported_name", name))
            alias = dict(imported.aliases).get(name)
            if alias:
                item_metadata.append(("imported_alias", alias))

            if len(child_files) == 1:
                target_file = child_files[0]
                target_module = "::".join(child_path)
                target_symbol = None
                status = "resolved"
            elif len(child_files) > 1:
                relationships.append(self._ambiguous_use(
                    module, imported, child_path, child_files, tuple(item_metadata), evidence,
                    target_symbol=name,
                    source_module=f"{package.crate_name}::{crate_context.target_name}",
                ))
                continue
            else:
                matches = [
                    symbol for symbol in symbol_candidates
                    if symbol.name == name
                    or symbol.qualified_name == name
                    or symbol.qualified_name.endswith("." + name)
                ]
                if len(matches) == 1:
                    target_file = base_file
                    target_module = "::".join(module_path) or package.crate_name
                    target_symbol = matches[0].qualified_name
                    status = "resolved"
                elif len(matches) > 1:
                    relationships.append(self._ambiguous_use(
                        module, imported, module_path,
                        tuple(f"{base_file}::{item.qualified_name}" for item in matches),
                        tuple(item_metadata), evidence, target_symbol=name,
                        source_module=f"{package.crate_name}::{crate_context.target_name}",
                    ))
                    continue
                else:
                    relationships.append(self._unresolved(
                        module, imported, "rust_symbol_not_found",
                        metadata=tuple(item_metadata), target_module="::".join(module_path),
                        source_module=f"{package.crate_name}::{crate_context.target_name}",
                    ))
                    continue

            relationships.append(Relationship(
                kind="imports",
                source_path=module.relative_path,
                source_module=f"{package.crate_name}::{crate_context.target_name}",
                source_symbol=None,
                target_path=target_file,
                target_module=target_module,
                target_symbol=target_symbol or (None if name == "self" else name),
                line=imported.line,
                status=status,
                evidence=evidence,
                metadata=tuple(item_metadata),
            ))
        return relationships

    @staticmethod
    def _ambiguous_use(module, imported, module_path, candidates, metadata, evidence, target_symbol=None, source_module=None):
        return Relationship(
            kind="imports", source_path=module.relative_path,
            source_module=source_module or (module.module_names[-1] if module.module_names else module.relative_path),
            source_symbol=None, target_path=None,
            target_module="::".join(module_path),
            target_symbol=target_symbol or ",".join(imported.imported_names) or None,
            line=imported.line, status="ambiguous", evidence=evidence,
            candidates=tuple(candidates), metadata=tuple(metadata),
        )

    @staticmethod
    def _module_path_for_source(relative_path, crate_context):
        matches = [
            module_path for module_path, paths in crate_context.module_files.items()
            if relative_path in paths
        ]
        # The empty path is the crate root. Prefer the physical file's module
        # mapping over inline-module aliases that point to the same file.
        return min(matches, key=len) if matches else ()

    @staticmethod
    def _local_dependency(package, dependency, context):
        if not dependency.path:
            return None
        candidate = (context.root / dependency.path_base / dependency.path).resolve()
        try:
            relative = candidate.relative_to(context.root).as_posix()
            if relative == ".":
                relative = ""
        except ValueError:
            return None
        return context.package_by_root.get(relative)

    @staticmethod
    def _external_use(module, imported, target, evidence, metadata, *, source_module=None):
        return Relationship(
            kind="imports", source_path=module.relative_path,
            source_module=source_module or (module.module_names[-1] if module.module_names else module.relative_path),
            source_symbol=None, target_path=None, target_module=target,
            target_symbol=",".join(imported.imported_names) or None,
            line=imported.line, status="external", evidence=evidence,
            metadata=tuple(metadata),
        )

    @staticmethod
    def _unresolved(module, imported, reason, *, metadata=(), status="unresolved", target_module=None, source_module=None):
        details = list(metadata)
        details.append(("reason", reason))
        return Relationship(
            kind="imports", source_path=module.relative_path,
            source_module=source_module or (module.module_names[-1] if module.module_names else module.relative_path),
            source_symbol=None, target_path=None,
            target_module=target_module or imported.module,
            target_symbol=",".join(imported.imported_names) or None,
            line=imported.line, status=status, evidence="rust_import_path",
            metadata=tuple(details),
        )

