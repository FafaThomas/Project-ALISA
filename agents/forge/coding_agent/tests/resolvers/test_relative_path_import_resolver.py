from coding_agent.code_index import ModuleRecord
from coding_agent.parsers.base import ImportRecord
from coding_agent.resolvers.relative_path_import_resolver import RelativePathImportResolver


def module(path, language, names, imports=()):
    return ModuleRecord(
        relative_path=path,
        module_names=names,
        symbols=(),
        imports=tuple(imports),
        language=language,
    )


def lookup(*modules):
    result = {}
    for item in modules:
        for alias in item.module_names:
            result.setdefault(alias, []).append(item)
    return {key: tuple(value) for key, value in result.items()}


def test_typescript_relative_import_resolves_extensionless_file():
    source = module(
        "src/app/main.tsx", "tsx", ("src.app.main",),
        (ImportRecord("./components/Button", ("default",), 0, 1),),
    )
    target = module("src/app/components/Button.tsx", "tsx", ("src.app.components.Button",))

    relationships = RelativePathImportResolver("tsx").resolve(source, lookup(source, target))

    assert len(relationships) == 1
    assert relationships[0].status == "resolved"
    assert relationships[0].target_path == "src/app/components/Button.tsx"


def test_javascript_relative_import_resolves_directory_index():
    source = module(
        "src/app/main.js", "javascript", ("src.app.main",),
        (ImportRecord("./components", (), 0, 2),),
    )
    target = module("src/app/components/index.ts", "typescript", ("src.app.components.index",))

    relationships = RelativePathImportResolver("javascript").resolve(source, lookup(source, target))

    assert relationships[0].status == "resolved"
    assert relationships[0].target_path == "src/app/components/index.ts"


def test_bare_package_import_is_classified_as_external():
    source = module(
        "src/app/main.js", "javascript", ("src.app.main",),
        (ImportRecord("react", ("default",), 0, 1),),
    )

    relationships = RelativePathImportResolver("javascript").resolve(source, lookup(source))

    assert relationships[0].status == "external"
    assert relationships[0].target_module == "react"


def test_configured_path_alias_is_not_guessed_as_external():
    source = module(
        "src/app/main.ts", "typescript", ("src.app.main",),
        (ImportRecord("@/components/Button", ("Button",), 0, 1),),
    )

    relationships = RelativePathImportResolver("typescript").resolve(source, lookup(source))

    assert relationships[0].status == "unresolved"


def test_typescript_resolution_prefers_typescript_over_javascript_file():
    source = module(
        "src/app/main.ts", "typescript", ("src.app.main",),
        (ImportRecord("./utils", (), 0, 1),),
    )
    javascript_target = module("src/app/utils.js", "javascript", ("src.app.utils.js",))
    typescript_target = module("src/app/utils.ts", "typescript", ("src.app.utils.ts",))

    relationships = RelativePathImportResolver("typescript").resolve(
        source, lookup(source, javascript_target, typescript_target)
    )

    assert relationships[0].target_path == "src/app/utils.ts"
