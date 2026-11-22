"""Language-aware import and relationship resolution."""

from coding_agent.resolvers.base import ImportResolver
from coding_agent.resolvers.defaults import build_default_import_resolver_registry
from coding_agent.resolvers.engine import RelationshipEngine
from coding_agent.resolvers.models import Relationship
from coding_agent.resolvers.registry import ImportResolverRegistry
from coding_agent.resolvers.python_import_resolver import PythonImportResolver
from coding_agent.resolvers.go_import_resolver import GoImportResolver
from coding_agent.resolvers.rust_import_resolver import RustImportResolver
from coding_agent.resolvers.relative_path_import_resolver import RelativePathImportResolver

__all__ = [
    "Relationship",
    "RelationshipEngine",
    "ImportResolver",
    "ImportResolverRegistry",
    "PythonImportResolver",
    "GoImportResolver",
    "RustImportResolver",
    "RelativePathImportResolver",
    "build_default_import_resolver_registry",
]
