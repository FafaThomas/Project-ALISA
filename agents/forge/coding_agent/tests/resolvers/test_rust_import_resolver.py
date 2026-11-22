from pathlib import Path

from coding_agent.code_index import ModuleRecord
from coding_agent.parsers.base import ImportRecord, Symbol
from coding_agent.resolvers import RustImportResolver


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def symbol(name, kind="function", path="src/lib.rs", *, qualified=None, metadata=(), decorators=(), line=1):
    return Symbol(
        name=name,
        qualified_name=qualified or name,
        kind=kind,
        relative_path=path,
        line=line,
        end_line=line,
        metadata=tuple(metadata),
        decorators=tuple(decorators),
    )


def module(path, imports=(), symbols=(), names=()):
    return ModuleRecord(
        relative_path=path,
        module_names=tuple(names) or (path.replace("/", ".").removesuffix(".rs"),),
        symbols=tuple(symbols),
        imports=tuple(imports),
        language="rust",
    )


def build_edges(root, *modules):
    resolver = RustImportResolver()
    lookup = {}
    for item in modules:
        for name in item.module_names:
            lookup.setdefault(name, []).append(item)
    context = resolver.create_context(modules, root)
    edges = []
    for item in modules:
        edges.extend(resolver.resolve_with_context(
            item, lookup, workspace_root=root, resolution_context=context
        ))
    return tuple(edges)


def test_rust_resolver_maps_crate_use_to_declared_module_file(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "demo-app"\nedition = "2021"\n')
    write(tmp_path / "src/lib.rs", "pub mod models;\n")
    write(tmp_path / "src/models.rs", "pub struct User;\n")

    root = module("src/lib.rs", (ImportRecord("crate::models", ("User",), 0, 4, kind="rust_use"),), (
        symbol("models", "module", metadata=(("module_form", "external"),)),
    ))
    models = module("src/models.rs", symbols=(symbol("User", "struct", "src/models.rs"),))
    edges = build_edges(tmp_path, root, models)

    declared = next(edge for edge in edges if edge.kind == "declares_module")
    imported = next(edge for edge in edges if edge.kind == "imports")
    assert declared.status == "resolved"
    assert declared.target_path == "src/models.rs"
    assert imported.status == "resolved"
    assert imported.target_path == "src/models.rs"
    assert imported.target_symbol == "User"


def test_rust_resolver_handles_nested_module_files(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "demo"\nedition = "2021"\n')
    write(tmp_path / "src/lib.rs", "mod outer;\n")
    write(tmp_path / "src/outer.rs", "mod inner;\n")
    write(tmp_path / "src/outer/inner.rs", "pub fn run() {}\n")

    root = module("src/lib.rs", symbols=(symbol("outer", "module", metadata=(("module_form", "external"),)),))
    outer = module("src/outer.rs", symbols=(symbol("inner", "module", "src/outer.rs", metadata=(("module_form", "external"),)),))
    inner = module("src/outer/inner.rs", symbols=(symbol("run", "function", "src/outer/inner.rs"),))
    edges = build_edges(tmp_path, root, outer, inner)

    nested = [edge for edge in edges if edge.kind == "declares_module"]
    assert {edge.target_path for edge in nested} == {"src/outer.rs", "src/outer/inner.rs"}
    assert all(edge.status == "resolved" for edge in nested)


def test_rust_resolver_preserves_inline_module_and_nested_external_module(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "demo"\nedition = "2021"\n')
    write(tmp_path / "src/lib.rs", "mod inline { mod child; }\n")
    write(tmp_path / "src/inline/child.rs", "pub struct Child;\n")

    root = module("src/lib.rs", symbols=(
        symbol("inline", "module", metadata=(("module_form", "inline"),)),
        symbol("child", "module", qualified="inline.child", metadata=(("module_form", "external"),)),
    ))
    child = module("src/inline/child.rs", symbols=(symbol("Child", "struct", "src/inline/child.rs"),))
    edges = build_edges(tmp_path, root, child)

    inline = next(edge for edge in edges if edge.target_module == "inline")
    child_edge = next(edge for edge in edges if edge.target_module == "inline::child")
    assert inline.target_path == "src/lib.rs"
    assert child_edge.target_path == "src/inline/child.rs"


def test_rust_resolver_supports_crate_self_and_super_paths(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "demo"\nedition = "2021"\n')
    write(tmp_path / "src/lib.rs", "mod parent;\n")
    write(tmp_path / "src/parent.rs", "pub struct Parent;\nmod child;\n")
    write(tmp_path / "src/parent/child.rs", "pub fn work() {}\n")

    root = module("src/lib.rs", symbols=(
        symbol("parent", "module", metadata=(("module_form", "external"),)),
        symbol("RootThing", "struct", "src/lib.rs"),
    ))
    parent = module("src/parent.rs", (
        ImportRecord("super", ("RootThing",), 0, 3, kind="rust_use"),
        ImportRecord("self::child", ("work",), 0, 4, kind="rust_use"),
    ), (
        symbol("Parent", "struct", "src/parent.rs"),
        symbol("child", "module", "src/parent.rs", metadata=(("module_form", "external"),)),
    ))
    child = module("src/parent/child.rs", symbols=(symbol("work", "function", "src/parent/child.rs"),))
    edges = build_edges(tmp_path, root, parent, child)

    imported = [edge for edge in edges if edge.kind == "imports" and edge.source_path == "src/parent.rs"]
    assert len(imported) == 2
    assert all(edge.status == "resolved" for edge in imported)


def test_rust_resolver_classifies_standard_crates_as_external(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "demo"\nedition = "2021"\n')
    write(tmp_path / "src/lib.rs", "")
    source = module("src/lib.rs", (
        ImportRecord("std::collections", ("HashMap",), 0, 1, kind="rust_use"),
        ImportRecord("core::fmt", ("Debug",), 0, 2, kind="rust_use"),
    ))
    edges = build_edges(tmp_path, source)
    assert [edge.status for edge in edges if edge.kind == "imports"] == ["external", "external"]


def test_rust_resolver_does_not_guess_undeclared_crate_as_external(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname = "demo"\nedition = "2021"\n')
    write(tmp_path / "src/lib.rs", "")
    source = module("src/lib.rs", (ImportRecord("unknown_crate::Thing", ("Thing",), 0, 1, kind="rust_use"),))
    edges = build_edges(tmp_path, source)
    edge = next(edge for edge in edges if edge.kind == "imports")
    assert edge.status == "unresolved"
    assert dict(edge.metadata)["reason"] == "crate_not_declared_as_dependency_or_local_module"


def test_rust_resolver_resolves_local_path_dependency_and_renamed_crate(tmp_path):
    write(tmp_path / "app/Cargo.toml", '[package]\nname="app"\nedition="2021"\n[dependencies]\nshared_alias = { package="shared-lib", path="../shared" }\n')
    write(tmp_path / "app/src/lib.rs", "")
    write(tmp_path / "shared/Cargo.toml", '[package]\nname="shared-lib"\nedition="2021"\n')
    write(tmp_path / "shared/src/lib.rs", "pub struct Model;\n")

    app = module("app/src/lib.rs", (ImportRecord("shared_alias", ("Model",), 0, 2, kind="rust_use"),))
    shared = module("shared/src/lib.rs", symbols=(symbol("Model", "struct", "shared/src/lib.rs"),))
    edges = build_edges(tmp_path, app, shared)

    imported = next(edge for edge in edges if edge.kind == "imports" and edge.source_path == "app/src/lib.rs")
    dependency = next(edge for edge in edges if edge.kind == "cargo_dependency")
    assert imported.status == "resolved"
    assert imported.target_path == "shared/src/lib.rs"
    assert dependency.status == "resolved"
    assert dependency.target_path == "shared/Cargo.toml"
    assert dict(imported.metadata)["imported_name"] == "Model"


def test_rust_resolver_records_external_cargo_dependencies(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname="app"\nedition="2021"\n[dependencies]\nserde = "1"\n')
    write(tmp_path / "src/lib.rs", "")
    source = module("src/lib.rs")
    edges = build_edges(tmp_path, source)
    edge = next(edge for edge in edges if edge.kind == "cargo_dependency")
    assert edge.status == "external"
    assert edge.target_module == "serde"
    assert dict(edge.metadata)["version"] == "1"


def test_rust_resolver_supports_workspace_inherited_path_dependencies(tmp_path):
    write(tmp_path / "Cargo.toml", '[workspace]\nmembers=["app", "shared"]\n[workspace.dependencies]\nshared={ path="shared" }\n')
    write(tmp_path / "app/Cargo.toml", '[package]\nname="app"\nedition="2021"\n[dependencies]\nshared={ workspace=true }\n')
    write(tmp_path / "app/src/lib.rs", "")
    write(tmp_path / "shared/Cargo.toml", '[package]\nname="shared"\nedition="2021"\n')
    write(tmp_path / "shared/src/lib.rs", "pub struct Item;\n")

    app = module("app/src/lib.rs", (ImportRecord("shared", ("Item",), 0, 1, kind="rust_use"),))
    shared = module("shared/src/lib.rs", symbols=(symbol("Item", "struct", "shared/src/lib.rs"),))
    edges = build_edges(tmp_path, app, shared)

    imported = next(edge for edge in edges if edge.kind == "imports")
    assert imported.status == "resolved"
    assert imported.target_path == "shared/src/lib.rs"


def test_rust_resolver_reports_missing_module_file_without_guessing(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname="demo"\nedition="2021"\n')
    write(tmp_path / "src/lib.rs", "")
    source = module("src/lib.rs", symbols=(symbol("missing", "module", metadata=(("module_form", "external"),)),))
    edges = build_edges(tmp_path, source)
    edge = next(edge for edge in edges if edge.kind == "declares_module")
    assert edge.status == "unresolved"
    assert edge.target_path is None


def test_rust_resolver_detects_ambiguous_module_file_candidates(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname="demo"\nedition="2021"\n')
    write(tmp_path / "src/lib.rs", "")
    write(tmp_path / "src/foo.rs", "")
    write(tmp_path / "src/foo/mod.rs", "")
    source = module("src/lib.rs", symbols=(symbol("foo", "module", metadata=(("module_form", "external"),)),))
    # Both physical candidates are indexed in this synthetic index.
    foo = module("src/foo.rs")
    foo_mod = module("src/foo/mod.rs")
    edges = build_edges(tmp_path, source, foo, foo_mod)
    edge = next(edge for edge in edges if edge.kind == "declares_module")
    assert edge.status == "ambiguous"
    assert set(edge.candidates) == {"src/foo.rs", "src/foo/mod.rs"}


def test_rust_resolver_preserves_import_alias_and_public_reexport_metadata(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname="demo"\nedition="2021"\n')
    write(tmp_path / "src/lib.rs", "pub mod models;\n")
    write(tmp_path / "src/models.rs", "pub struct User;\n")
    source = module("src/lib.rs", (
        ImportRecord("crate::models", ("User",), 0, 3, kind="rust_use", is_public=True, aliases=(("User", "Account"),)),
    ), (symbol("models", "module", metadata=(("module_form", "external"),)),))
    models = module("src/models.rs", symbols=(symbol("User", "struct", "src/models.rs"),))
    edges = build_edges(tmp_path, source, models)
    edge = next(edge for edge in edges if edge.kind == "imports")
    assert edge.status == "resolved"
    assert dict(edge.metadata)["is_public"] == "true"
    assert dict(edge.metadata)["imported_alias"] == "Account"


def test_rust_resolver_honors_path_attribute_for_out_of_line_modules(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname="demo"\nedition="2021"\n')
    write(tmp_path / "src/lib.rs", "")
    write(tmp_path / "src/custom/models.rs", "pub struct User;\n")
    source = module("src/lib.rs", symbols=(symbol(
        "models", "module", metadata=(("module_form", "external"),),
        decorators=('#[path = "custom/models.rs"]',),
    ),))
    target = module("src/custom/models.rs", symbols=(symbol("User", "struct", "src/custom/models.rs"),))

    edges = build_edges(tmp_path, source, target)
    edge = next(edge for edge in edges if edge.kind == "declares_module")
    assert edge.status == "resolved"
    assert edge.target_path == "src/custom/models.rs"


def test_rust_resolver_reports_local_path_dependency_outside_workspace(tmp_path):
    write(tmp_path / "app/Cargo.toml", '[package]\nname="app"\nedition="2021"\n[dependencies]\nshared={ path="../../outside/shared" }\n')
    write(tmp_path / "app/src/lib.rs", "")
    app = module("app/src/lib.rs")

    edges = build_edges(tmp_path, app)
    edge = next(edge for edge in edges if edge.kind == "cargo_dependency")
    assert edge.status == "unresolved"
    assert dict(edge.metadata)["reason"] == "local_dependency_outside_workspace"


def test_rust_resolver_preserves_dev_build_and_target_dependency_kinds(tmp_path):
    write(tmp_path / "Cargo.toml", '''
[package]
name = "demo"
edition = "2021"
[dependencies]
serde = "1"
[dev-dependencies]
pretty_assertions = "1"
[build-dependencies]
cc = "1"
[target.'cfg(unix)'.dependencies]
unix_dep = "2"
''')
    write(tmp_path / "src/lib.rs", "")
    edges = build_edges(tmp_path, module("src/lib.rs"))
    kinds = {dict(edge.metadata)["dependency_alias"]: dict(edge.metadata)["dependency_kind"]
             for edge in edges if edge.kind == "cargo_dependency"}
    assert kinds == {
        "serde": "dependencies",
        "pretty_assertions": "dev-dependencies",
        "cc": "build-dependencies",
        "unix_dep": "dependencies",
    }


def test_rust_resolver_does_not_resolve_package_name_mismatch_for_path_dependency(tmp_path):
    write(tmp_path / "app/Cargo.toml", '[package]\nname="app"\nedition="2021"\n[dependencies]\nshared={ package="expected-name", path="../shared" }\n')
    write(tmp_path / "app/src/lib.rs", "")
    write(tmp_path / "shared/Cargo.toml", '[package]\nname="different-name"\nedition="2021"\n')
    write(tmp_path / "shared/src/lib.rs", "")
    app = module("app/src/lib.rs")
    shared = module("shared/src/lib.rs")

    edges = build_edges(tmp_path, app, shared)
    edge = next(edge for edge in edges if edge.kind == "cargo_dependency")
    assert edge.status == "unresolved"
    assert dict(edge.metadata)["reason"] == "local_dependency_manifest_not_found"


def test_rust_resolver_supports_path_dependency_to_workspace_root_package(tmp_path):
    write(tmp_path / "Cargo.toml", '[package]\nname="shared-root"\nedition="2021"\n')
    write(tmp_path / "src/lib.rs", "pub struct Shared;\n")
    write(tmp_path / "app/Cargo.toml", '[package]\nname="app"\nedition="2021"\n[dependencies]\nshared={ package="shared-root", path=".." }\n')
    write(tmp_path / "app/src/lib.rs", "")
    app = module("app/src/lib.rs", (ImportRecord("shared", ("Shared",), 0, 1, kind="rust_use"),))
    shared = module("src/lib.rs", symbols=(symbol("Shared", "struct", "src/lib.rs"),))

    edges = build_edges(tmp_path, app, shared)
    imported = next(edge for edge in edges if edge.kind == "imports" and edge.source_path == "app/src/lib.rs")
    dependency = next(edge for edge in edges if edge.kind == "cargo_dependency")
    assert imported.status == "resolved"
    assert imported.target_path == "src/lib.rs"
    assert dependency.status == "resolved"
    assert dependency.target_path == "Cargo.toml"


def test_code_index_build_uses_default_rust_resolver_with_workspace_context(tmp_path):
    from coding_agent.code_index import CodeIndexer
    from coding_agent.parsers import ParsedSource, ParserRegistry
    from coding_agent.workspace import Workspace

    write(tmp_path / "Cargo.toml", '[package]\nname="demo"\nedition="2021"\n')
    write(tmp_path / "src/lib.rs", "mod models; use crate::models::User;\n")
    write(tmp_path / "src/models.rs", "pub struct User;\n")

    class FixtureRustParser:
        language = "rust"
        extensions = (".rs",)

        def parse(self, relative_path, source):
            if relative_path == "src/lib.rs":
                return ParsedSource(
                    symbols=(symbol("models", "module", relative_path,
                                    metadata=(("module_form", "external"),)),),
                    imports=(ImportRecord("crate::models", ("User",), 0, 1, kind="rust_use"),),
                )
            return ParsedSource(
                symbols=(symbol("User", "struct", relative_path),),
                imports=(),
            )

    index = CodeIndexer(
        Workspace(tmp_path), parser_registry=ParserRegistry([FixtureRustParser()])
    ).build()
    edge = next(
        item for item in index.relationships
        if item.kind == "imports" and item.source_path == "src/lib.rs"
    )
    assert edge.status == "resolved"
    assert edge.target_path == "src/models.rs"


def test_default_resolver_registry_registers_rust():
    from coding_agent.resolvers.defaults import build_default_import_resolver_registry

    assert isinstance(build_default_import_resolver_registry().get("rust"), RustImportResolver)


def test_rust_resolver_uses_workspace_inherited_edition(tmp_path):
    write(tmp_path / "Cargo.toml", '[workspace]\nmembers=["app"]\n[workspace.package]\nedition="2021"\n')
    write(tmp_path / "app/Cargo.toml", '[package]\nname="app"\nedition.workspace=true\n')
    write(tmp_path / "app/src/lib.rs", "")
    resolver = RustImportResolver()
    context = resolver.create_context((module("app/src/lib.rs"),), tmp_path)
    package = next(item for item in context.packages if item.name == "app")
    assert package.edition == "2021"
