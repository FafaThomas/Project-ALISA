import pytest

from coding_agent.parsers import CSharpParser


@pytest.fixture
def parser():
    return CSharpParser()


def test_csharp_parser_metadata(parser):
    assert parser.language == "csharp"
    assert parser.extensions == (".cs",)


def test_extracts_csharp_types_methods_and_namespace(parser):
    source = """
using System;
namespace Demo.Services
{
    public interface IUserService
    {
        User GetById(int id);
    }

    public class UserService : IUserService
    {
        public User GetById(int id)
        {
            return new User(id);
        }
    }
}
"""
    parsed = parser.parse("Services/UserService.cs", source)
    found = {(item.qualified_name, item.kind) for item in parsed.symbols}

    assert ("Demo.Services", "namespace") in found
    assert ("Demo.Services.IUserService", "interface") in found
    assert ("Demo.Services.IUserService.GetById", "method") in found
    assert ("Demo.Services.UserService", "class") in found
    assert ("Demo.Services.UserService.GetById", "method") in found


def test_extracts_csharp_using_directives(parser):
    parsed = parser.parse(
        "Program.cs",
        "using System;\nusing Collections = System.Collections.Generic;\n"
        "class Program { }\n",
    )
    assert [item.module for item in parsed.imports] == [
        "System",
        "System.Collections.Generic",
    ]
    assert parsed.imports[1].imported_names == ("Collections",)


def test_csharp_parser_rejects_syntax_errors(parser):
    with pytest.raises(SyntaxError):
        parser.parse("Broken.cs", "public class {")
