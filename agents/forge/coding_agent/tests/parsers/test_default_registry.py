from coding_agent.parsers import (
    CSharpParser,
    JavaParser,
    PythonParser,
    build_default_registry,
)


def test_default_registry_supports_python_csharp_and_java():
    registry = build_default_registry()

    assert isinstance(registry.get_parser("src/main.py"), PythonParser)
    assert isinstance(registry.get_parser("src/Program.cs"), CSharpParser)
    assert isinstance(registry.get_parser("src/Main.java"), JavaParser)
    assert registry.get_parser("README.md").language == "markdown"


def test_default_registry_extension_lookup_is_case_insensitive():
    registry = build_default_registry()

    assert isinstance(registry.get_parser("src/Program.CS"), CSharpParser)
    assert isinstance(registry.get_parser("src/Main.JAVA"), JavaParser)
