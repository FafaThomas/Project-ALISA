"""Language-specific source parsers and shared parser contracts."""

from coding_agent.parsers.base import (
    ImportRecord, ParsedSource, ParseDiagnostic, ParseFailure, ReferenceRecord, SourceParser, Symbol,
)
from coding_agent.parsers.csharp_parser import CSharpParser
from coding_agent.parsers.defaults import build_default_registry
from coding_agent.parsers.java_parser import JavaParser
from coding_agent.parsers.python_parser import PythonParser
from coding_agent.parsers.registry import ParserRegistry
from coding_agent.parsers.javascript_parser import JavaScriptParser
from coding_agent.parsers.typescript_parser import TypeScriptParser
from coding_agent.parsers.tsx_parser import TSXParser
from coding_agent.parsers.html_parser import HTMLParser
from coding_agent.parsers.css_parser import CSSParser
from coding_agent.parsers.scss_parser import SCSSParser
from coding_agent.parsers.sql_parser import SQLParser
from coding_agent.parsers.json_parser import JSONParser
from coding_agent.parsers.yaml_parser import YAMLParser
from coding_agent.parsers.toml_parser import TOMLParser
from coding_agent.parsers.xml_parser import XMLParser
from coding_agent.parsers.go_parser import GoParser
from coding_agent.parsers.rust_parser import RustParser
from coding_agent.parsers.c_parser import CParser
from coding_agent.parsers.cpp_parser import CppParser
from coding_agent.parsers.php_parser import PHPParser
from coding_agent.parsers.ruby_parser import RubyParser
from coding_agent.parsers.kotlin_parser import KotlinParser
from coding_agent.parsers.swift_parser import SwiftParser
from coding_agent.parsers.bash_parser import BashParser
from coding_agent.parsers.powershell_parser import PowerShellParser
from coding_agent.parsers.dockerfile_parser import DockerfileParser
from coding_agent.parsers.terraform_parser import TerraformParser
from coding_agent.parsers.markdown_parser import MarkdownParser
from coding_agent.parsers.jsx_parser import JSXParser

__all__ = [
    "ImportRecord", "ParsedSource", "ParseDiagnostic", "ParseFailure", "ReferenceRecord", "SourceParser", "Symbol",
    "PythonParser", "CSharpParser", "JavaParser", "ParserRegistry",
    "build_default_registry",
    'JavaScriptParser',
    'TypeScriptParser',
    'TSXParser',
    'HTMLParser',
    'CSSParser',
    'SCSSParser',
    'SQLParser',
    'JSONParser',
    'YAMLParser',
    'TOMLParser',
    'XMLParser',
    'GoParser',
    'RustParser',
    'CParser',
    'CppParser',
    'PHPParser',
    'RubyParser',
    'KotlinParser',
    'SwiftParser',
    'BashParser',
    'PowerShellParser',
    'DockerfileParser',
    'TerraformParser',
    'MarkdownParser',
    'JSXParser',
]
