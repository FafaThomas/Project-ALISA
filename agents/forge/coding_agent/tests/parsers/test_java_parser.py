import pytest

from coding_agent.parsers import JavaParser


@pytest.fixture
def parser():
    return JavaParser()


def test_java_parser_metadata(parser):
    assert parser.language == "java"
    assert parser.extensions == (".java",)


def test_extracts_java_types_and_methods(parser):
    source = """
package com.example.services;

import java.util.List;

public interface UserRepository {
    User findById(String id);
}

public class UserService {
    public User findUser(String id) {
        return null;
    }
}
"""
    parsed = parser.parse("UserService.java", source)
    found = {(item.qualified_name, item.kind) for item in parsed.symbols}

    assert ("com.example.services", "package") in found
    assert ("com.example.services.UserRepository", "interface") in found
    assert (
        "com.example.services.UserRepository.findById",
        "method",
    ) in found
    assert ("com.example.services.UserService", "class") in found
    assert (
        "com.example.services.UserService.findUser",
        "method",
    ) in found


def test_extracts_java_imports(parser):
    parsed = parser.parse(
        "Main.java",
        "import java.util.List;\n"
        "import static java.util.Collections.emptyList;\n"
        "class Main { }\n",
    )
    assert [item.module for item in parsed.imports] == [
        "java.util.List",
        "java.util.Collections.emptyList",
    ]


def test_java_parser_rejects_syntax_errors(parser):
    with pytest.raises(SyntaxError):
        parser.parse("Broken.java", "public class {")
