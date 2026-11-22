
from coding_agent.code_index import CodeIndexer
from coding_agent.parsers import ParserRegistry, PythonParser
from coding_agent.workspace import Workspace



def make_workspace(tmp_path):
    return Workspace(tmp_path)


def test_extracts_top_level_functions_and_classes(tmp_path):
    (tmp_path / "service.py").write_text(
        """
class UserService:
    def create_user(self, name):
        return name

    async def fetch_user(self, user_id):
        return user_id


def validate_user(name):
    return bool(name)
""",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    names = {
        (symbol.qualified_name, symbol.kind)
        for symbol in index.symbols
    }

    assert ("UserService", "class") in names
    assert ("UserService.create_user", "method") in names
    assert ("UserService.fetch_user", "method") in names
    assert ("validate_user", "function") in names


def test_extracts_nested_class_methods(tmp_path):
    (tmp_path / "models.py").write_text(
        """
class Outer:
    class Inner:
        def execute(self):
            return True
""",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    symbol = index.find_symbol("Outer.Inner.execute")

    assert len(symbol) == 1
    assert symbol[0].kind == "method"


def test_extracts_import_statements(tmp_path):
    (tmp_path / "main.py").write_text(
        """
import os
import json as json_lib
from pathlib import Path
from collections import defaultdict, deque
""",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()
    module = index.find_module("main")[0]

    assert len(module.imports) == 4
    assert module.imports[0].module == "os"
    assert module.imports[1].module == "json"
    assert module.imports[2].module == "pathlib"
    assert module.imports[3].imported_names == (
        "defaultdict",
        "deque",
    )


def test_resolves_internal_imports_in_src_layout(tmp_path):
    package = tmp_path / "src" / "myapp"
    package.mkdir(parents=True)

    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "models.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from myapp.models import User\n",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    assert any(
        dependency.source_module == "myapp.service"
        and dependency.target_module == "myapp.models"
        for dependency in index.dependencies
    )


def test_resolves_relative_imports(tmp_path):
    package = tmp_path / "myapp"
    package.mkdir()

    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "models.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from .models import User\n",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    assert any(
        dependency.source_module == "myapp.service"
        and dependency.target_module == "myapp.models"
        for dependency in index.dependencies
    )


def test_finds_symbols_by_simple_and_qualified_name(tmp_path):
    (tmp_path / "service.py").write_text(
        """
class UserService:
    def create(self):
        pass
""",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    assert len(index.find_symbol("UserService")) == 1
    assert len(index.find_symbol("create")) == 1
    assert len(index.find_symbol("UserService.create")) == 1


def test_finds_dependents_of_a_module(tmp_path):
    package = tmp_path / "myapp"
    package.mkdir()

    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "models.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from myapp.models import User\n",
        encoding="utf-8",
    )
    (package / "api.py").write_text(
        "from myapp.models import User\n",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    dependents = index.dependents_of("myapp.models")

    assert len(dependents) == 2
    assert {
        dependency.source_module for dependency in dependents
    } == {
        "myapp.service",
        "myapp.api",
    }


def test_ignores_generated_directories(tmp_path):
    source = tmp_path / "src"
    source.mkdir()

    generated = tmp_path / "__pycache__"
    generated.mkdir()

    (source / "main.py").write_text(
        "def main(): pass",
        encoding="utf-8",
    )
    (generated / "compiled.py").write_text(
        "def compiled(): pass",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    paths = {module.relative_path for module in index.modules}

    assert "src/main.py" in paths
    assert "__pycache__/compiled.py" not in paths


def test_records_syntax_errors_without_aborting_index(tmp_path):
    (tmp_path / "valid.py").write_text(
        "VALUE = 42",
        encoding="utf-8",
    )
    (tmp_path / "invalid.py").write_text(
        "def broken(:\n    pass",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    assert index.indexed_files == 1
    assert index.failed_files == 1

    invalid = index.find_module("invalid")[0]
    assert invalid.parse_error is not None


def test_respects_maximum_file_count(tmp_path):
    for number in range(5):
        (tmp_path / f"module_{number}.py").write_text(
            f"VALUE = {number}",
            encoding="utf-8",
        )

    index = CodeIndexer(
        make_workspace(tmp_path),
        max_files=2,
    ).build()

    assert index.discovered_files == 2
    assert index.indexed_files == 2


def test_render_contains_structure_and_dependencies(tmp_path):
    package = tmp_path / "myapp"
    package.mkdir()

    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "models.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from myapp.models import User\n",
        encoding="utf-8",
    )

    rendered = CodeIndexer(make_workspace(tmp_path)).build().render()

    assert "PYTHON CODE STRUCTURE" in rendered
    assert "class: User" in rendered
    assert "INTERNAL DEPENDENCIES:" in rendered
    assert "myapp.service -> myapp.models" in rendered


def test_rejects_invalid_configuration(tmp_path):
    try:
        CodeIndexer(make_workspace(tmp_path), max_files=0)
    except ValueError as exc:
        assert "max_files" in str(exc)
    else:
        raise AssertionError("Expected invalid configuration to fail")

def test_python_parser_parses_source_independently():
    parser = PythonParser()

    parsed = parser.parse(
        "service.py",
        """
class UserService:
    def create_user(self, name):
        return name


def validate_user(name):
    return bool(name)
""",
    )

    names = {
        (symbol.qualified_name, symbol.kind)
        for symbol in parsed.symbols
    }

    assert ("UserService", "class") in names
    assert ("UserService.create_user", "method") in names
    assert ("validate_user", "function") in names


def test_parser_registry_resolves_python_files():
    registry = ParserRegistry([PythonParser()])

    parser = registry.get_parser("src/service.py")

    assert isinstance(parser, PythonParser)
    assert registry.get_parser("src/service.cs") is None
    assert registry.supported_extensions == (".py",)


def test_indexer_indexes_supported_extensions_and_ignores_unsupported(
    tmp_path,
):
    (tmp_path / "main.py").write_text(
        "def main():\n    pass\n",
        encoding="utf-8",
    )

    (tmp_path / "Program.cs").write_text(
        "public class Program { }",
        encoding="utf-8",
    )

    (tmp_path / "notes.xyz").write_text(
        "This is an unsupported file format.",
        encoding="utf-8",
    )

    index = CodeIndexer(make_workspace(tmp_path)).build()

    modules = {
        module.relative_path: module
        for module in index.modules
    }

    assert set(modules) == {"main.py", "Program.cs"}

    assert modules["main.py"].language == "python"
    assert modules["Program.cs"].language == "csharp"

    assert index.discovered_files == 2
    assert index.indexed_files == 2


def test_code_index_preserves_parser_references_for_later_resolution(tmp_path):
    from coding_agent.parsers.base import ParsedSource, ReferenceRecord

    class ReferenceParser:
        language = "reference-test"
        extensions = (".ref",)

        def parse(self, relative_path, source):
            return ParsedSource(
                symbols=(),
                imports=(),
                references=(
                    ReferenceRecord("Customer", "schema_reference", 4, "orders.customer_id"),
                ),
            )

    (tmp_path / "schema.ref").write_text("reference source", encoding="utf-8")
    registry = ParserRegistry([ReferenceParser()])
    index = CodeIndexer(make_workspace(tmp_path), parser_registry=registry).build()

    assert index.modules[0].references[0].target == "Customer"
    assert "reference (schema_reference): Customer" in index.render()
