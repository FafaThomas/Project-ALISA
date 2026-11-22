"""Python import resolution against workspace module aliases."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence, TYPE_CHECKING

from coding_agent.resolvers.models import Relationship

if TYPE_CHECKING:
    from coding_agent.code_index import ModuleRecord


class PythonImportResolver:
    language = "python"

    @staticmethod
    def _resolve_relative_import(
        source_module: str,
        import_record,
        is_package: bool,
    ) -> str | None:
        if import_record.relative_level == 0:
            return import_record.module

        parts = source_module.split(".")
        if not is_package:
            parts = parts[:-1]

        remove_count = import_record.relative_level - 1
        if remove_count > len(parts):
            return None
        if remove_count:
            parts = parts[:len(parts) - remove_count]
        if import_record.module:
            parts.extend(import_record.module.split("."))
        return ".".join(parts) if parts else None

    def resolve(
        self,
        module: "ModuleRecord",
        module_lookup: Mapping[str, Sequence["ModuleRecord"]],
    ) -> tuple[Relationship, ...]:
        if not module.module_names:
            return ()

        source_name = module.module_names[-1]
        is_package = Path(module.relative_path).name == "__init__.py"
        relationships: list[Relationship] = []

        for imported in module.imports:
            target_name = self._resolve_relative_import(
                source_name, imported, is_package
            )
            if not target_name:
                relationships.append(Relationship(
                    kind="imports",
                    source_path=module.relative_path,
                    source_module=source_name,
                    source_symbol=None,
                    target_path=None,
                    target_module=None,
                    target_symbol=imported.module,
                    line=imported.line,
                    status="unresolved",
                    evidence=imported.kind,
                    metadata=(("reason", "relative_import_outside_package"),),
                ))
                continue

            candidates = [
                f"{target_name}.{name}"
                for name in imported.imported_names
                if name != "*"
            ]
            candidates.append(target_name)

            selected_alias = None
            selected_modules: Sequence[ModuleRecord] = ()
            for candidate in candidates:
                matches = module_lookup.get(candidate, ())
                if matches:
                    selected_alias = candidate
                    selected_modules = matches
                    break

            if not selected_modules:
                relationships.append(Relationship(
                    kind="imports",
                    source_path=module.relative_path,
                    source_module=source_name,
                    source_symbol=None,
                    target_path=None,
                    target_module=target_name,
                    target_symbol=None,
                    line=imported.line,
                    status="unresolved",
                    evidence=imported.kind,
                    metadata=(("imported_names", ",".join(imported.imported_names)),),
                ))
                continue

            if len(selected_modules) > 1:
                relationships.append(Relationship(
                    kind="imports",
                    source_path=module.relative_path,
                    source_module=source_name,
                    source_symbol=None,
                    target_path=None,
                    target_module=selected_alias,
                    target_symbol=None,
                    line=imported.line,
                    status="ambiguous",
                    evidence=imported.kind,
                    candidates=tuple(sorted(item.relative_path for item in selected_modules)),
                    metadata=(("imported_names", ",".join(imported.imported_names)),),
                ))
                continue

            target_module = selected_modules[0]
            relationships.append(Relationship(
                kind="imports",
                source_path=module.relative_path,
                source_module=source_name,
                source_symbol=None,
                target_path=target_module.relative_path,
                target_module=selected_alias,
                target_symbol=None,
                line=imported.line,
                status="resolved",
                evidence=imported.kind,
                metadata=(("imported_names", ",".join(imported.imported_names)),),
            ))

        return tuple(relationships)
