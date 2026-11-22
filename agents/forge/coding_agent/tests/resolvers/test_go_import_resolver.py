from pathlib import Path

from coding_agent.code_index import CodeIndexer, ModuleRecord
from coding_agent.parsers.base import ImportRecord, ParsedSource
from coding_agent.parsers.registry import ParserRegistry
from coding_agent.resolvers.go_import_resolver import GoImportResolver
from coding_agent.workspace import Workspace


def make_module(path, imports=(), language="go", names=()):
    return ModuleRecord(
        relative_path=path,
        symbols=(),
        imports=tuple(imports),
        language=language,
        module_names=tuple(names) or (path.rsplit("/", 1)[-1].rsplit(".", 1)[0],),
    )


def lookup(*modules):
    result = {}
    for item in modules:
        for alias in item.module_names:
            result.setdefault(alias, []).append(item)
    return {key: tuple(value) for key, value in result.items()}


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_go_resolver_maps_module_paths_to_indexed_package_directories(tmp_path):
    write(tmp_path / "go.mod", "module github.com/acme/shop\n\ngo 1.23\n")
    write(tmp_path / "cmd" / "api" / "main.go", "package main\n")
    write(tmp_path / "internal" / "store" / "store.go", "package store\n")

    source = make_module(
        "cmd/api/main.go",
        (ImportRecord("github.com/acme/shop/internal/store", (), 0, 4, kind="go_import"),),
    )
    target = make_module("internal/store/store.go")
    resolver = GoImportResolver()
    discovered = resolver._discover_workspace(tmp_path)
    assert discovered.modules[0].go_version == "1.23"

    relationships = resolver.resolve_with_context(
        source, lookup(source, target), workspace_root=tmp_path
    )

    assert len(relationships) == 1
    edge = relationships[0]
    assert edge.status == "resolved"
    assert edge.source_module == "github.com/acme/shop/cmd/api"
    assert edge.target_module == "github.com/acme/shop/internal/store"
    assert edge.target_path == "internal/store/store.go"
    assert edge.evidence == "go_module_path"


def test_go_resolver_does_not_collide_same_package_names_in_different_directories(tmp_path):
    write(tmp_path / "go.mod", "module example.com/project\n")
    write(tmp_path / "one" / "models.go", "package model\n")
    write(tmp_path / "two" / "models.go", "package model\n")

    source = make_module(
        "main.go",
        (ImportRecord("example.com/project/two", (), 0, 2, kind="go_import"),),
    )
    one = make_module("one/models.go")
    two = make_module("two/models.go")

    edge = GoImportResolver().resolve_with_context(
        source, lookup(source, one, two), workspace_root=tmp_path
    )[0]

    assert edge.status == "resolved"
    assert edge.target_path == "two/models.go"



def test_go_resolver_keeps_missing_package_under_local_module_unresolved(tmp_path):
    write(tmp_path / "go.mod", "module example.com/app\n")
    source = make_module(
        "main.go",
        (ImportRecord("example.com/app/internal/missing", (), 0, 1, kind="go_import"),),
    )

    edge = GoImportResolver().resolve_with_context(
        source, lookup(source), workspace_root=tmp_path
    )[0]

    assert edge.status == "unresolved"
    assert dict(edge.metadata)["reason"] == "package_directory_not_indexed"

def test_go_resolver_preserves_alias_blank_and_dot_import_semantics(tmp_path):
    write(tmp_path / "go.mod", "module example.com/app\n")
    write(tmp_path / "lib" / "lib.go", "package lib\n")
    imports = (
        ImportRecord("example.com/app/lib", (), 0, 1, kind="go_import", alias="custom"),
        ImportRecord("image/png", (), 0, 2, kind="go_import", alias="_"),
        ImportRecord("example.com/app/lib", (), 0, 3, kind="go_import", alias="."),
    )
    source = make_module("main.go", imports)
    target = make_module("lib/lib.go")

    edges = GoImportResolver().resolve_with_context(
        source, lookup(source, target), workspace_root=tmp_path
    )

    assert [edge.status for edge in edges] == ["resolved", "external", "resolved"]
    assert dict(edges[0].metadata)["import_alias"] == "custom"
    assert dict(edges[0].metadata)["import_semantics"] == "aliased_import"
    assert dict(edges[1].metadata)["import_semantics"] == "blank_import"
    assert dict(edges[2].metadata)["import_semantics"] == "dot_import"


def test_go_resolver_classifies_standard_library_and_dotted_modules_as_external(tmp_path):
    write(tmp_path / "go.mod", "module example.com/app\n")
    source = make_module(
        "main.go",
        (
            ImportRecord("net/http", (), 0, 1, kind="go_import"),
            ImportRecord("github.com/other/project/pkg", (), 0, 2, kind="go_import"),
        ),
    )

    edges = GoImportResolver().resolve_with_context(source, lookup(source), workspace_root=tmp_path)

    assert [edge.status for edge in edges] == ["external", "external"]


def test_go_resolver_does_not_guess_unknown_internal_imports_are_external(tmp_path):
    write(tmp_path / "go.mod", "module example.com/app\n")
    source = make_module(
        "main.go",
        (ImportRecord("internal/missing", (), 0, 1, kind="go_import"),),
    )

    edge = GoImportResolver().resolve_with_context(source, lookup(source), workspace_root=tmp_path)[0]

    assert edge.status == "unresolved"
    assert dict(edge.metadata)["reason"] == "not_a_known_external_or_local_module"


def test_go_resolver_uses_nearest_nested_module_root(tmp_path):
    write(tmp_path / "go.mod", "module example.com/outer\n")
    write(tmp_path / "nested" / "go.mod", "module example.com/nested\n")
    write(tmp_path / "nested" / "pkg" / "pkg.go", "package pkg\n")
    source = make_module(
        "nested/cmd/main.go",
        (ImportRecord("example.com/nested/pkg", (), 0, 1, kind="go_import"),),
    )
    target = make_module("nested/pkg/pkg.go")

    edge = GoImportResolver().resolve_with_context(
        source, lookup(source, target), workspace_root=tmp_path
    )[0]

    assert edge.status == "resolved"
    assert edge.source_module == "example.com/nested/cmd"
    assert edge.target_path == "nested/pkg/pkg.go"


def test_go_resolver_maps_local_replace_directive_to_replacement_package(tmp_path):
    write(
        tmp_path / "app" / "go.mod",
        "module example.com/app\n\nreplace example.com/shared => ../shared\n",
    )
    write(tmp_path / "app" / "main.go", "package main\n")
    write(tmp_path / "shared" / "go.mod", "module example.com/shared-local\n")
    write(tmp_path / "shared" / "models" / "models.go", "package models\n")

    source = make_module(
        "app/main.go",
        (ImportRecord("example.com/shared/models", (), 0, 5, kind="go_import"),),
    )
    target = make_module("shared/models/models.go")

    edge = GoImportResolver().resolve_with_context(
        source, lookup(source, target), workspace_root=tmp_path
    )[0]

    assert edge.status == "resolved"
    assert edge.target_module == "example.com/shared/models"
    assert edge.target_path == "shared/models/models.go"


def test_go_resolver_does_not_misclassify_out_of_workspace_local_replace_as_external(tmp_path):
    write(
        tmp_path / "app" / "go.mod",
        "module example.com/app\nreplace example.com/shared => ../../outside\n",
    )
    write(tmp_path / "app" / "main.go", "package main\n")
    source = make_module(
        "app/main.go",
        (ImportRecord("example.com/shared/pkg", (), 0, 1, kind="go_import"),),
    )

    edge = GoImportResolver().resolve_with_context(
        source, lookup(source), workspace_root=tmp_path
    )[0]

    assert edge.status == "unresolved"
    assert dict(edge.metadata)["reason"] == "local_replacement_outside_workspace"

def test_go_work_use_directives_are_discovered_and_nested_module_imports_resolve(tmp_path):
    write(tmp_path / "go.work", "go 1.23\n\nuse (\n    ./app\n    ./shared\n)\n")
    write(tmp_path / "app" / "go.mod", "module example.com/app\n")
    write(tmp_path / "shared" / "go.mod", "module example.com/shared\n")
    write(tmp_path / "app" / "main.go", "package main\n")
    write(tmp_path / "shared" / "lib.go", "package shared\n")

    source = make_module(
        "app/main.go",
        (ImportRecord("example.com/shared", (), 0, 3, kind="go_import"),),
    )
    target = make_module("shared/lib.go")
    resolver = GoImportResolver()
    workspace = resolver._discover_workspace(tmp_path)

    assert set(workspace.workspace_uses) == {"app", "shared"}
    edge = resolver.resolve_with_context(source, lookup(source, target), workspace_root=tmp_path)[0]
    assert edge.status == "resolved"
    assert edge.target_path == "shared/lib.go"


def test_go_work_only_activates_modules_listed_in_use_directives(tmp_path):
    write(tmp_path / "go.work", "go 1.23\n\nuse ./app\n")
    write(tmp_path / "app" / "go.mod", "module example.com/app\n")
    write(tmp_path / "hidden" / "go.mod", "module example.com/hidden\n")
    write(tmp_path / "app" / "main.go", "package main\n")
    write(tmp_path / "hidden" / "lib.go", "package hidden\n")

    source = make_module(
        "app/main.go",
        (ImportRecord("example.com/hidden", (), 0, 1, kind="go_import"),),
    )
    hidden = make_module("hidden/lib.go")

    edge = GoImportResolver().resolve_with_context(
        source, lookup(source, hidden), workspace_root=tmp_path
    )[0]

    assert edge.status == "external"
    assert edge.target_path is None


def test_go_work_local_replace_resolves_module_not_listed_in_use(tmp_path):
    write(
        tmp_path / "go.work",
        "go 1.23\n\nuse ./app\n\nreplace example.com/shared => ./shared\n",
    )
    write(tmp_path / "app" / "go.mod", "module example.com/app\n")
    write(tmp_path / "shared" / "go.mod", "module example.com/shared-local\n")
    write(tmp_path / "app" / "main.go", "package main\n")
    write(tmp_path / "shared" / "lib.go", "package shared\n")

    source = make_module(
        "app/main.go",
        (ImportRecord("example.com/shared", (), 0, 1, kind="go_import"),),
    )
    target = make_module("shared/lib.go")

    edge = GoImportResolver().resolve_with_context(
        source, lookup(source, target), workspace_root=tmp_path
    )[0]

    assert edge.status == "resolved"
    assert edge.target_path == "shared/lib.go"
    assert edge.target_module == "example.com/shared"

def test_code_index_passes_workspace_root_to_go_resolver(tmp_path):
    write(tmp_path / "go.mod", "module example.com/app\n")
    write(tmp_path / "main.go", 'package main\nimport "example.com/app/lib"\n')
    write(tmp_path / "lib" / "lib.go", "package lib\n")

    class FixtureGoParser:
        language = "go"
        extensions = (".go",)

        def parse(self, relative_path, source):
            imports = ()
            if relative_path == "main.go":
                imports = (ImportRecord(
                    "example.com/app/lib", (), 0, 2, kind="go_import"
                ),)
            return ParsedSource(symbols=(), imports=imports)

    registry = ParserRegistry([FixtureGoParser()])
    index = CodeIndexer(
        Workspace(tmp_path), parser_registry=registry
    ).build()
    edge = next(
        item for item in index.relationships
        if item.kind == "imports" and item.source_path == "main.go"
    )

    assert edge.status == "resolved"
    assert edge.target_path == "lib/lib.go"
