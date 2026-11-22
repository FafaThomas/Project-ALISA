from coding_agent.code_index import ModuleRecord
from coding_agent.parsers.base import ImportRecord
from coding_agent.resolvers.python_import_resolver import PythonImportResolver


def make_module(path, names, imports=()):
    return ModuleRecord(
        relative_path=path,
        module_names=names,
        symbols=(),
        imports=tuple(imports),
        language="python",
    )


def test_python_import_resolver_marks_duplicate_module_aliases_ambiguous():
    source = make_module(
        "app/service.py",
        ("app.service",),
        (ImportRecord("models", (), 0, 1),),
    )
    left = make_module("project_a/models.py", ("models",))
    right = make_module("project_b/models.py", ("models",))

    relationships = PythonImportResolver().resolve(
        source,
        {"models": (left, right)},
    )

    assert len(relationships) == 1
    assert relationships[0].status == "ambiguous"
    assert relationships[0].candidates == (
        "project_a/models.py",
        "project_b/models.py",
    )


def test_python_import_resolver_retains_unresolved_imports():
    source = make_module(
        "app/service.py",
        ("app.service",),
        (ImportRecord("external_package", (), 0, 3),),
    )

    relationships = PythonImportResolver().resolve(source, {})

    assert len(relationships) == 1
    assert relationships[0].status == "unresolved"
    assert relationships[0].target_module == "external_package"


def test_import_resolver_registry_rejects_duplicate_language():
    import pytest
    from coding_agent.resolvers.registry import ImportResolverRegistry

    registry = ImportResolverRegistry()
    resolver = PythonImportResolver()
    registry.register(resolver)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(PythonImportResolver())
