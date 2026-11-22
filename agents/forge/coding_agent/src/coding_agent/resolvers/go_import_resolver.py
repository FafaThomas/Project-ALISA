"""Resolve Go imports using go.mod, go.work, and indexed package directories.

Resolution is intentionally static: it never invokes ``go list`` or downloads
modules. Imports are resolved only when the target package directory is
represented by indexed source files or a known local replacement.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence, TYPE_CHECKING

from coding_agent.resolvers.models import Relationship

if TYPE_CHECKING:
    from coding_agent.code_index import ModuleRecord


_IGNORED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    "vendor", "dist", "build", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox",
}

# Standard-library top-level import paths. This is a classification aid only;
# unknown paths without a dotted first component remain unresolved.
_STDLIB = frozenset("""
archive bufio builtin bytes cmp compress container context crypto database
debug embed encoding errors expvar flag fmt go hash html image index io iter log
maps math mime net os path plugin reflect regexp runtime slices sort
strconv strings structs sync syscall testing text time unicode unique unsafe weak
""".split())

_MODULE_RE = re.compile(r'^\s*module\s+(?:"([^"]+)"|`([^`]+)`|(\S+))')


@dataclass(frozen=True)
class GoModule:
    root: str
    import_path: str
    go_version: str | None = None
    replacements: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GoWorkspace:
    root: Path
    modules: tuple[GoModule, ...]
    workspace_uses: tuple[str, ...] = ()
    workspace_replacements: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GoResolverContext:
    workspace: GoWorkspace
    package_directories: dict[str, str]


class GoImportResolver:
    language = "go"

    def resolve(
        self,
        module: "ModuleRecord",
        module_lookup: Mapping[str, Sequence["ModuleRecord"]],
    ) -> tuple[Relationship, ...]:
        """Implement the common resolver contract when no workspace context exists."""
        return self.resolve_with_context(module, module_lookup, workspace_root=None)

    def create_context(self, modules, workspace_root) -> GoResolverContext | None:
        """Build workspace metadata and package mappings once per index operation."""
        if workspace_root is None:
            return None
        root = Path(workspace_root).resolve()
        workspace = self._discover_workspace(root)
        indexed = tuple(
            item for item in modules
            if item.language == "go" and item.parse_error is None
        )
        return GoResolverContext(
            workspace=workspace,
            package_directories=self._package_directories(indexed, workspace),
        )

    def resolve_with_context(
        self,
        module: "ModuleRecord",
        module_lookup: Mapping[str, Sequence["ModuleRecord"]],
        *,
        workspace_root=None,
        resolution_context: GoResolverContext | None = None,
    ) -> tuple[Relationship, ...]:
        if workspace_root is None:
            return tuple(
                self._unresolved(module, imported, None, "workspace_root_unavailable")
                for imported in module.imports
            )

        context = resolution_context or self.create_context(
            self._indexed_modules(module_lookup), workspace_root
        )
        if context is None:
            return tuple(
                self._unresolved(module, imported, None, "workspace_root_unavailable")
                for imported in module.imports
            )
        workspace = context.workspace
        package_dirs = context.package_directories
        source_package = self._package_import_path(module.relative_path, workspace)

        relationships = []
        for imported in module.imports:
            specifier = imported.module or ""
            target_path = self._resolve_import_path(specifier, workspace)
            target_dir = package_dirs.get(target_path) if target_path else None

            metadata = [
                ("imported_names", ",".join(imported.imported_names)),
                ("import_alias", imported.alias or ""),
                ("import_kind", imported.kind),
            ]
            if imported.alias == "_":
                metadata.append(("import_semantics", "blank_import"))
            elif imported.alias == ".":
                metadata.append(("import_semantics", "dot_import"))
            elif imported.alias:
                metadata.append(("import_semantics", "aliased_import"))

            if target_dir is not None:
                relationships.append(Relationship(
                    kind="imports",
                    source_path=module.relative_path,
                    source_module=source_package or module.relative_path,
                    source_symbol=None,
                    target_path=target_dir,
                    target_module=target_path,
                    target_symbol=None,
                    line=imported.line,
                    status="resolved",
                    evidence="go_module_path",
                    metadata=tuple(metadata),
                ))
                continue

            # A path under a known local module (or local replacement) is
            # internal even when its package directory is absent from the
            # index. Never downgrade that missing local package to external.
            known_local = self._matches_local_prefix(specifier, workspace)
            if target_path is None and not known_local and self._is_external(specifier):
                relationships.append(Relationship(
                    kind="imports",
                    source_path=module.relative_path,
                    source_module=source_package or module.relative_path,
                    source_symbol=None,
                    target_path=None,
                    target_module=specifier,
                    target_symbol=None,
                    line=imported.line,
                    status="external",
                    evidence="go_import_path",
                    metadata=tuple(metadata),
                ))
                continue

            reason = (
                "package_directory_not_indexed"
                if target_path is not None
                else (
                    "local_replacement_outside_workspace"
                    if known_local
                    else "not_a_known_external_or_local_module"
                )
            )
            relationships.append(self._unresolved(
                module, imported, source_package, reason, metadata=tuple(metadata),
                target_module=specifier or None,
            ))

        return tuple(relationships)

    @staticmethod
    def _indexed_modules(module_lookup):
        unique = {}
        for group in module_lookup.values():
            for item in group:
                unique[item.relative_path] = item
        return tuple(unique.values())

    @classmethod
    def _discover_workspace(cls, root: Path) -> GoWorkspace:
        module_files: list[Path] = []
        work_file: Path | None = None

        for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            dirnames[:] = [
                name for name in dirnames
                if name.lower() not in _IGNORED_DIRS
                and not (current_path / name).is_symlink()
            ]
            if "go.mod" in filenames:
                candidate = current_path / "go.mod"
                if not candidate.is_symlink():
                    module_files.append(candidate)
            if current_path == root and "go.work" in filenames:
                candidate = current_path / "go.work"
                if not candidate.is_symlink():
                    work_file = candidate

        modules = []
        for mod_file in sorted(module_files):
            try:
                content = mod_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            module_path = cls._parse_module_path(content)
            if not module_path:
                continue
            module_root = mod_file.parent.resolve()
            try:
                relative_root = module_root.relative_to(root).as_posix()
            except ValueError:
                continue
            replacements = cls._parse_replacements(content)
            modules.append(GoModule(
                root="" if relative_root == "." else relative_root,
                import_path=module_path,
                go_version=cls._parse_go_version(content),
                replacements=tuple(replacements),
            ))

        uses = cls._parse_go_work(work_file, root) if work_file else ()
        try:
            work_content = (
                work_file.read_text(encoding="utf-8") if work_file else ""
            )
        except (OSError, UnicodeError):
            work_content = ""
        workspace_replacements = (
            cls._parse_replacements(work_content) if work_file else []
        )

        # In workspace mode, only modules named by go.work's use directives
        # are main workspace modules. Other nested go.mod files are not
        # implicitly promoted into the workspace.
        if work_file is not None:
            selected_roots = {"" if item == "." else item for item in uses}
            modules = [item for item in modules if item.root in selected_roots]

        return GoWorkspace(
            root,
            tuple(modules),
            tuple(uses),
            tuple(workspace_replacements),
        )

    @staticmethod
    def _parse_module_path(content: str) -> str | None:
        in_block = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or not stripped:
                continue
            if stripped.startswith("module "):
                match = _MODULE_RE.match(line)
                if match:
                    return next((item for item in match.groups() if item), None)
            if stripped.startswith("module ("):
                in_block = True
            elif in_block and stripped == ")":
                in_block = False
        return None

    @staticmethod
    def _parse_go_version(content: str) -> str | None:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("go "):
                parts = stripped.split()
                if len(parts) >= 2:
                    return parts[1]
        return None

    @staticmethod
    def _parse_replacements(content: str) -> list[tuple[str, str]]:
        replacements = []
        in_block = False
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if stripped == "replace (":
                in_block = True
                continue
            if in_block and stripped == ")":
                in_block = False
                continue
            if stripped.startswith("replace "):
                candidate = stripped[len("replace "):]
            elif in_block:
                candidate = stripped
            else:
                continue
            try:
                tokens = shlex.split(candidate, comments=True)
            except ValueError:
                continue
            if "=>" not in tokens:
                continue
            arrow = tokens.index("=>")
            left_parts = tokens[:arrow]
            right_parts = tokens[arrow + 1:]
            if not left_parts or not right_parts:
                continue
            old_path = left_parts[0]
            new_path = right_parts[0]
            if new_path.startswith((".", "/")) or re.match(r"^[A-Za-z]:[\\/]", new_path):
                replacements.append((old_path, new_path))
        return replacements

    @staticmethod
    def _parse_go_work(work_file: Path, root: Path) -> list[str]:
        try:
            lines = work_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return []
        uses = []
        in_block = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if stripped == "use (":
                in_block = True
                continue
            if in_block and stripped == ")":
                in_block = False
                continue
            if stripped.startswith("use "):
                candidate = stripped[4:].strip()
            elif in_block:
                candidate = stripped
            else:
                continue
            try:
                values = shlex.split(candidate, comments=True)
            except ValueError:
                continue
            if not values:
                continue
            value = values[0]
            resolved = (root / value).resolve()
            try:
                uses.append(resolved.relative_to(root).as_posix())
            except ValueError:
                # Never follow a go.work use path outside the indexed workspace.
                continue
        return uses

    @classmethod
    def _package_directories(cls, indexed, workspace: GoWorkspace) -> dict[str, str]:
        result: dict[str, str] = {}
        directories: dict[str, str] = {}
        conflicts: set[str] = set()

        def add_package(import_path: str, relative_file: str) -> None:
            directory = PurePosixPath(relative_file).parent.as_posix()
            existing_directory = directories.get(import_path)
            if existing_directory is None:
                directories[import_path] = directory
                result[import_path] = relative_file
            elif existing_directory == directory:
                # Multiple Go files in one folder represent the same package.
                result[import_path] = min(result[import_path], relative_file)
            else:
                conflicts.add(import_path)
                result.pop(import_path, None)

        for item in indexed:
            if item.language != "go" or item.parse_error is not None:
                continue
            package_path = cls._package_import_path(item.relative_path, workspace)
            if package_path is not None and package_path not in conflicts:
                add_package(package_path, item.relative_path)

        # A local replace directive gives the old module path a second,
        # workspace-local route to the replacement module's package folders.
        replacement_specs = [
            (
                old_path,
                (workspace.root / owner.root / local_path).resolve(),
            )
            for owner in workspace.modules
            for old_path, local_path in owner.replacements
        ]
        replacement_specs.extend(
            (old_path, (workspace.root / local_path).resolve())
            for old_path, local_path in workspace.workspace_replacements
        )

        for old_path, replacement_root in replacement_specs:
            try:
                replacement_relative = replacement_root.relative_to(
                    workspace.root
                ).as_posix()
            except ValueError:
                continue
            replacement_prefix = (
                PurePosixPath(replacement_relative)
                if replacement_relative != "." else PurePosixPath()
            )
            for item in indexed:
                if item.language != "go" or item.parse_error is not None:
                    continue
                item_dir = PurePosixPath(item.relative_path).parent
                try:
                    suffix = item_dir.relative_to(replacement_prefix).as_posix()
                except ValueError:
                    continue
                if suffix == ".":
                    suffix = ""
                alias_path = old_path if not suffix else f"{old_path}/{suffix}"
                if alias_path not in conflicts:
                    add_package(alias_path, item.relative_path)

        return result

    @staticmethod
    def _owner(relative_path: str, workspace: GoWorkspace) -> GoModule | None:
        path = PurePosixPath(relative_path)
        owners = []
        for item in workspace.modules:
            root_parts = PurePosixPath(item.root).parts if item.root else ()
            if path.parts[:len(root_parts)] == root_parts:
                owners.append((len(root_parts), item))
        return max(owners, key=lambda pair: pair[0])[1] if owners else None

    @classmethod
    def _package_import_path(cls, relative_path: str, workspace: GoWorkspace) -> str | None:
        owner = cls._owner(relative_path, workspace)
        if owner is None:
            return None
        module_root = PurePosixPath(owner.root) if owner.root else PurePosixPath()
        directory = PurePosixPath(relative_path).parent
        try:
            suffix = directory.relative_to(module_root).as_posix()
        except ValueError:
            return None
        if suffix == ".":
            suffix = ""
        return owner.import_path if not suffix else f"{owner.import_path}/{suffix}"

    @classmethod
    def _resolve_import_path(cls, specifier: str, workspace: GoWorkspace) -> str | None:
        # Exact module path or a package beneath a known module path.
        for module in sorted(workspace.modules, key=lambda item: len(item.import_path), reverse=True):
            if specifier == module.import_path or specifier.startswith(module.import_path + "/"):
                return specifier

        # Local replace maps an external-looking import prefix to an indexed
        # directory. Keep the original import path as the relationship identity.
        replacement_prefixes = [
            (old_path, (workspace.root / module.root / local_path).resolve())
            for module in workspace.modules
            for old_path, local_path in module.replacements
        ]
        replacement_prefixes.extend(
            (old_path, (workspace.root / local_path).resolve())
            for old_path, local_path in workspace.workspace_replacements
        )
        for old_path, replacement_root in replacement_prefixes:
            if specifier != old_path and not specifier.startswith(old_path + "/"):
                continue
            try:
                replacement_root.relative_to(workspace.root)
            except ValueError:
                continue
            return specifier
        return None

    @staticmethod
    def _matches_local_prefix(specifier: str, workspace: GoWorkspace) -> bool:
        if any(
            specifier == item.import_path
            or specifier.startswith(item.import_path + "/")
            for item in workspace.modules
        ):
            return True
        replacements = [
            old_path
            for item in workspace.modules
            for old_path, _ in item.replacements
        ] + [old_path for old_path, _ in workspace.workspace_replacements]
        return any(
            specifier == old_path or specifier.startswith(old_path + "/")
            for old_path in replacements
        )

    @staticmethod
    def _is_external(specifier: str) -> bool:
        if not specifier:
            return False
        first = specifier.split("/", 1)[0]
        if "." in first:
            return True
        return first in _STDLIB

    @staticmethod
    def _unresolved(module, imported, source_package, reason, *, metadata=(), target_module=None):
        return Relationship(
            kind="imports",
            source_path=module.relative_path,
            source_module=source_package or module.relative_path,
            source_symbol=None,
            target_path=None,
            target_module=target_module if target_module is not None else imported.module,
            target_symbol=None,
            line=imported.line,
            status="unresolved",
            evidence=imported.kind,
            metadata=tuple(metadata) + (("reason", reason),),
        )
