from coding_agent.code_index import CodeIndexer, ModuleRecord
from coding_agent.parsers.base import ImportRecord, ReferenceRecord, Symbol
from coding_agent.resolvers import RelationshipEngine
from coding_agent.workspace import Workspace


def module(path, language, symbols=(), imports=(), references=(), names=None):
    return ModuleRecord(
        relative_path=path,
        module_names=names or (path.rsplit("/", 1)[-1].rsplit(".", 1)[0],),
        symbols=tuple(symbols),
        imports=tuple(imports),
        language=language,
        references=tuple(references),
    )


def symbol(path, name, qualified, kind, line, end_line, metadata=()):
    return Symbol(
        name=name,
        qualified_name=qualified,
        kind=kind,
        relative_path=path,
        line=line,
        end_line=end_line,
        metadata=tuple(metadata),
    )


def test_relationship_engine_derives_lexical_containment():
    path = "app/service.py"
    outer = symbol(path, "Outer", "Outer", "class", 1, 8)
    inner = symbol(path, "Inner", "Outer.Inner", "class", 2, 7)
    method = symbol(path, "run", "Outer.Inner.run", "method", 3, 4)

    relationships = RelationshipEngine().resolve((module(
        path, "python", (outer, inner, method), names=("service",)
    ),))

    edges = {
        (item.kind, item.source_symbol, item.target_symbol)
        for item in relationships
    }
    assert ("contains", "Outer", "Outer.Inner") in edges
    assert ("contains", "Outer.Inner", "Outer.Inner.run") in edges


def test_relationship_engine_resolves_inheritance_metadata():
    base_path = "src/domain/base.java"
    child_path = "src/domain/child.java"
    base = symbol(base_path, "Base", "domain.Base", "class", 1, 4)
    child = symbol(
        child_path, "Child", "domain.Child", "class", 1, 5,
        metadata=(("extends", "Base"), ("implements", "Repository, Serializable")),
    )
    repository = symbol(base_path, "Repository", "domain.Repository", "interface", 6, 9)

    relationships = RelationshipEngine().resolve((
        module(base_path, "java", (base, repository), names=("domain.base",)),
        module(child_path, "java", (child,), names=("domain.child",)),
    ))

    by_kind = {(item.kind, item.target_symbol, item.status) for item in relationships}
    assert ("inherits", "domain.Base", "resolved") in by_kind
    assert ("implements", "domain.Repository", "resolved") in by_kind
    assert ("implements", "Serializable", "unresolved") in by_kind


def test_ambiguous_references_are_preserved_without_guessing():
    left_path = "db/left.sql"
    right_path = "db/right.sql"
    query_path = "db/report.sql"
    left_table = symbol(left_path, "users", "users", "table", 1, 5)
    right_table = symbol(right_path, "users", "users", "table", 1, 5)
    query = module(
        query_path,
        "sql",
        references=(ReferenceRecord("users", "table_reference", 2),),
    )

    relationships = RelationshipEngine().resolve((
        module(left_path, "sql", (left_table,)),
        module(right_path, "sql", (right_table,)),
        query,
    ))
    reference = next(item for item in relationships if item.kind == "table_reference")

    assert reference.status == "ambiguous"
    assert reference.target_path is None
    assert len(reference.candidates) == 2


def test_unresolved_parser_references_remain_in_relationship_graph():
    path = "web/page.html"
    relationships = RelationshipEngine().resolve((module(
        path,
        "html",
        references=(ReferenceRecord("assets/logo.svg", "html_asset", 4),),
    ),))

    assert len(relationships) == 1
    assert relationships[0].status == "unresolved"
    assert relationships[0].target_symbol == "assets/logo.svg"


def test_code_index_keeps_legacy_dependencies_and_exposes_typed_relationships(tmp_path):
    package = tmp_path / "myapp"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    (package / "service.py").write_text(
        "from myapp.models import User\n\nclass Service:\n    def get(self):\n        return User()\n",
        encoding="utf-8",
    )

    index = CodeIndexer(Workspace(tmp_path)).build()

    assert any(
        dependency.source_module == "myapp.service"
        and dependency.target_module == "myapp.models"
        for dependency in index.dependencies
    )
    assert any(
        relationship.kind == "imports"
        and relationship.source_module == "myapp.service"
        and relationship.target_module == "myapp.models"
        and relationship.status == "resolved"
        for relationship in index.relationships
    )
    assert any(
        relationship.kind == "contains"
        and relationship.target_symbol == "Service.get"
        for relationship in index.relationships
    )


def test_html_asset_reference_resolves_to_indexed_workspace_file():
    html_path = "web/index.html"
    script_path = "web/app.js"
    html = module(
        html_path,
        "html",
        references=(ReferenceRecord("./app.js?v=1", "html_asset", 8),),
    )
    script = module(script_path, "javascript")

    relationships = RelationshipEngine().resolve((html, script))
    asset = next(item for item in relationships if item.kind == "html_asset")

    assert asset.status == "resolved"
    assert asset.target_path == script_path


def test_stylesheet_import_resolves_extensionless_css_file():
    source_path = "web/styles/main.scss"
    target_path = "web/styles/tokens.scss"
    source = module(
        source_path,
        "scss",
        references=(ReferenceRecord("./tokens", "stylesheet_import", 1),),
    )
    target = module(target_path, "scss")

    relationships = RelationshipEngine().resolve((source, target))
    imported = next(item for item in relationships if item.kind == "stylesheet_import")

    assert imported.status == "resolved"
    assert imported.target_path == target_path
