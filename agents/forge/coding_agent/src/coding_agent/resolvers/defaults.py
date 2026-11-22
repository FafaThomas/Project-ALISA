"""Default language-specific import resolver registry."""

from coding_agent.resolvers.python_import_resolver import PythonImportResolver
from coding_agent.resolvers.go_import_resolver import GoImportResolver
from coding_agent.resolvers.rust_import_resolver import RustImportResolver
from coding_agent.resolvers.registry import ImportResolverRegistry
from coding_agent.resolvers.relative_path_import_resolver import RelativePathImportResolver


def build_default_import_resolver_registry() -> ImportResolverRegistry:
    registry = ImportResolverRegistry()
    registry.register(PythonImportResolver())
    registry.register(GoImportResolver())
    registry.register(RustImportResolver())
    for language in ("javascript", "typescript", "jsx", "tsx"):
        registry.register(RelativePathImportResolver(language))
    return registry
